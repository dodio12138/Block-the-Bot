// Reachable placement search + features.
#include <algorithm>
#include <cmath>
#include <deque>
#include <unordered_map>

#include "tbt/core.hpp"

namespace tbt {

Features compute_features(const Board& b, const Ruleset& r) {
  Features f;
  const int W = b.width(), H = b.height();
  for (int x = 0; x < W; ++x) {
    const int h = b.column_height(x);
    f.heights[x] = h;
    f.aggregate_height += h;
    f.max_height = std::max(f.max_height, h);
    for (int y = 0; y < h; ++y)
      if (!b.filled(x, y)) ++f.holes;
  }
  for (int x = 0; x + 1 < W; ++x) f.bumpiness += std::abs(f.heights[x] - f.heights[x + 1]);

  // Row transitions over rows that contain blocks (walls count as filled).
  for (int y = 0; y < f.max_height; ++y) {
    bool prev = true;
    for (int x = 0; x < W; ++x) {
      const bool cur = b.filled(x, y);
      if (cur != prev) ++f.row_transitions;
      prev = cur;
    }
    if (!prev) ++f.row_transitions;
  }
  // Column transitions (floor counts as filled).
  for (int x = 0; x < W; ++x) {
    bool prev = true;
    for (int y = 0; y < H; ++y) {
      const bool cur = b.filled(x, y);
      if (cur != prev) ++f.col_transitions;
      prev = cur;
    }
  }
  // Cumulative wells.
  for (int x = 0; x < W; ++x) {
    int depth = 0;
    for (int y = H - 1; y >= 0; --y) {
      if (!b.filled(x, y) && b.filled(x - 1, y) && b.filled(x + 1, y)) {
        ++depth;
        f.wells_sum += depth;
      } else {
        depth = 0;
      }
    }
    const int big = 1 << 20;
    const int l = x == 0 ? big : f.heights[x - 1];
    const int rr = x == W - 1 ? big : f.heights[x + 1];
    const int m = std::min(l, rr);
    if (m != big) f.max_well = std::max(f.max_well, m - f.heights[x]);
  }

  // Danger near the spawn zone.
  const int vis = r.visible_height;
  int spawn_h = 0;
  for (int x = std::max(0, W / 2 - 2); x < std::min(W, W / 2 + 2); ++x) spawn_h = std::max(spawn_h, f.heights[x]);
  auto ramp = [&](int h) { return std::clamp((h - (vis - 8)) / 8.0, 0.0, 1.0); };
  f.top_risk = std::max(ramp(spawn_h), 0.75 * ramp(f.max_height));
  f.risk = std::clamp(0.45 * f.max_height / static_cast<double>(vis) + 0.2 * std::min(f.holes / 10.0, 1.0) +
                          0.1 * std::min(f.bumpiness / 25.0, 1.0) + 0.25 * f.top_risk,
                      0.0, 1.0);
  return f;
}

namespace {

uint64_t cells_key(const Cells& c, int width) {
  std::array<int, 4> idx;
  for (int i = 0; i < 4; ++i) idx[i] = c[i].y * width + c[i].x;
  std::sort(idx.begin(), idx.end());
  uint64_t k = 0;
  for (int v : idx) k = (k << 12) | static_cast<uint64_t>(v & 0xFFF);
  return k;
}

}  // namespace

std::vector<Candidate> search_placements(const Board& b, const Ruleset& r, Piece p) {
  std::vector<Candidate> out;
  const PieceState sp = spawn_state(p, r);
  if (b.collides(piece_cells(p, sp))) return out;

  const int W = b.width(), H = b.height();
  const int SX = W + 4, SY = H + 4;
  const int N = 4 * SX * SY;
  auto in_range = [&](const PieceState& s) { return s.x >= -3 && s.x < W + 1 && s.y >= 0 && s.y < SY; };
  auto encode = [&](const PieceState& s) { return (s.rot * SY + s.y) * SX + (s.x + 3); };
  auto decode = [&](int i) {
    PieceState s;
    s.rot = i / (SX * SY);
    const int rem = i % (SX * SY);
    s.y = rem / SX;
    s.x = rem % SX - 3;
    return s;
  };
  auto ok = [&](const PieceState& s) { return in_range(s) && !b.collides(piece_cells(p, s)); };

  std::vector<int> parent(static_cast<size_t>(N), -2);
  std::deque<int> queue;
  const int start = encode(sp);
  parent[start] = -1;
  queue.push_back(start);

  std::unordered_map<uint64_t, int> seen;  // cell set -> first landing state
  std::vector<int> landings;

  auto visit = [&](const PieceState& n, int from) {
    if (!ok(n)) return;
    const int ni = encode(n);
    if (parent[ni] != -2) return;
    parent[ni] = from;
    queue.push_back(ni);
  };

  while (!queue.empty()) {
    const int ci = queue.front();
    queue.pop_front();
    const PieceState s = decode(ci);

    PieceState down = s;
    down.y -= 1;
    if (!ok(down)) {
      const uint64_t key = cells_key(piece_cells(p, s), W);
      if (seen.emplace(key, ci).second) landings.push_back(ci);
    } else {
      visit(down, ci);
    }
    visit({s.x - 1, s.y, s.rot}, ci);
    visit({s.x + 1, s.y, s.rot}, ci);
    for (int dir = 0; dir < 2; ++dir) {
      const bool cw = dir == 0;
      const int nr = cw ? (s.rot + 1) & 3 : (s.rot + 3) & 3;
      for (const Cell& k : kick_table(p, s.rot, cw)) {
        PieceState n{s.x + k.x, s.y + k.y, nr};
        if (ok(n)) {
          visit(n, ci);
          break;
        }
      }
    }
  }

  out.reserve(landings.size());
  for (int li : landings) {
    Candidate c;
    c.piece = p;
    c.state = decode(li);
    c.cells = piece_cells(p, c.state);
    for (int i = li; i != -1; i = parent[i]) c.path.push_back(decode(i));
    std::reverse(c.path.begin(), c.path.end());

    int min_y = 1 << 20, max_y = -1;
    bool above = true;
    for (const Cell& k : c.cells) {
      min_y = std::min(min_y, k.y);
      max_y = std::max(max_y, k.y);
      if (k.y < r.visible_height) above = false;
    }
    if (p == Piece::T) {
      // 3-corner rule + immobility (cannot move up/left/right).
      const int cx = c.state.x + 1, cy = c.state.y - 1;
      int corners = 0;
      corners += b.filled(cx - 1, cy + 1);
      corners += b.filled(cx + 1, cy + 1);
      corners += b.filled(cx - 1, cy - 1);
      corners += b.filled(cx + 1, cy - 1);
      const bool immobile = !ok({c.state.x, c.state.y + 1, c.state.rot}) &&
                            !ok({c.state.x - 1, c.state.y, c.state.rot}) &&
                            !ok({c.state.x + 1, c.state.y, c.state.rot});
      c.tspin = corners >= 3 && immobile;
    }

    Board after = b;
    after.place(c.cells);
    c.lines = after.clear_lines();
    c.features = compute_features(after, r);
    c.features.landing_height = (min_y + max_y) / 2.0 + 1.0;
    if (r.topout == TopOutRule::LockOut) {
      c.features.topout = above;
    } else {
      c.features.topout = c.features.max_height > r.visible_height;
    }
    out.push_back(std::move(c));
  }

  std::sort(out.begin(), out.end(), [](const Candidate& a, const Candidate& b2) {
    int ax = 99, bx = 99;
    for (const Cell& k : a.cells) ax = std::min(ax, k.x);
    for (const Cell& k : b2.cells) bx = std::min(bx, k.x);
    if (a.state.rot != b2.state.rot) return a.state.rot < b2.state.rot;
    if (ax != bx) return ax < bx;
    return a.state.y > b2.state.y;
  });
  return out;
}

}  // namespace tbt
