#include "tbt/core.hpp"

namespace tbt {

namespace {

struct ShapeTable {
  std::array<std::array<Cells, 4>, kPieceCount> shapes{};
  std::array<int, kPieceCount> box{{4, 2, 3, 3, 3, 3, 3}};

  ShapeTable() {
    // Spawn orientation, y-down box coordinates (guideline shapes).
    static const int base[kPieceCount][4][2] = {
        {{0, 1}, {1, 1}, {2, 1}, {3, 1}},  // I
        {{0, 0}, {1, 0}, {0, 1}, {1, 1}},  // O
        {{1, 0}, {0, 1}, {1, 1}, {2, 1}},  // T
        {{1, 0}, {2, 0}, {0, 1}, {1, 1}},  // S
        {{0, 0}, {1, 0}, {1, 1}, {2, 1}},  // Z
        {{0, 0}, {0, 1}, {1, 1}, {2, 1}},  // J
        {{2, 0}, {0, 1}, {1, 1}, {2, 1}},  // L
    };
    for (int p = 0; p < kPieceCount; ++p) {
      for (int i = 0; i < 4; ++i) shapes[p][0][i] = {base[p][i][0], base[p][i][1]};
      const int n = box[p];
      for (int r = 1; r < 4; ++r) {
        for (int i = 0; i < 4; ++i) {
          const Cell prev = shapes[p][r - 1][i];
          shapes[p][r][i] = {n - 1 - prev.y, prev.x};  // clockwise rotation in the box
        }
      }
    }
  }
};

const ShapeTable& table() {
  static const ShapeTable t;
  return t;
}

using Kick5 = std::array<Cell, 5>;

// SRS kicks, +y up. Index by from-rotation (0, R, 2, L).
const Kick5 kJlstzCw[4] = {
    {{{0, 0}, {-1, 0}, {-1, 1}, {0, -2}, {-1, -2}}},  // 0 -> R
    {{{0, 0}, {1, 0}, {1, -1}, {0, 2}, {1, 2}}},      // R -> 2
    {{{0, 0}, {1, 0}, {1, 1}, {0, -2}, {1, -2}}},     // 2 -> L
    {{{0, 0}, {-1, 0}, {-1, -1}, {0, 2}, {-1, 2}}},   // L -> 0
};
const Kick5 kJlstzCcw[4] = {
    {{{0, 0}, {1, 0}, {1, 1}, {0, -2}, {1, -2}}},     // 0 -> L
    {{{0, 0}, {1, 0}, {1, -1}, {0, 2}, {1, 2}}},      // R -> 0
    {{{0, 0}, {-1, 0}, {-1, 1}, {0, -2}, {-1, -2}}},  // 2 -> R
    {{{0, 0}, {-1, 0}, {-1, -1}, {0, 2}, {-1, 2}}},   // L -> 2
};
const Kick5 kICw[4] = {
    {{{0, 0}, {-2, 0}, {1, 0}, {-2, -1}, {1, 2}}},  // 0 -> R
    {{{0, 0}, {-1, 0}, {2, 0}, {-1, 2}, {2, -1}}},  // R -> 2
    {{{0, 0}, {2, 0}, {-1, 0}, {2, 1}, {-1, -2}}},  // 2 -> L
    {{{0, 0}, {1, 0}, {-2, 0}, {1, -2}, {-2, 1}}},  // L -> 0
};
const Kick5 kICcw[4] = {
    {{{0, 0}, {-1, 0}, {2, 0}, {-1, 2}, {2, -1}}},  // 0 -> L
    {{{0, 0}, {2, 0}, {-1, 0}, {2, 1}, {-1, -2}}},  // R -> 0
    {{{0, 0}, {1, 0}, {-2, 0}, {1, -2}, {-2, 1}}},  // 2 -> R
    {{{0, 0}, {-2, 0}, {1, 0}, {-2, -1}, {1, 2}}},  // L -> 2
};
const Kick5 kNone = {{{0, 0}, {0, 0}, {0, 0}, {0, 0}, {0, 0}}};

}  // namespace

char piece_char(Piece p) { return "IOTSZJL"[pidx(p)]; }

bool piece_from_char(char c, Piece& out) {
  static const char* names = "IOTSZJL";
  if (c >= 'a' && c <= 'z') c = static_cast<char>(c - 'a' + 'A');
  for (int i = 0; i < kPieceCount; ++i) {
    if (names[i] == c) {
      out = static_cast<Piece>(i);
      return true;
    }
  }
  return false;
}

bool Ruleset::valid() const {
  return width >= 4 && width <= kMaxWidth && visible_height >= 4 && hidden_rows >= 2 &&
         total_height() <= kMaxHeight;
}

const Cells& shape_offsets(Piece p, int rot) { return table().shapes[pidx(p)][rot & 3]; }

int box_size(Piece p) { return table().box[pidx(p)]; }

Cells piece_cells(Piece p, const PieceState& s) {
  const Cells& o = shape_offsets(p, s.rot);
  Cells c;
  for (int i = 0; i < 4; ++i) c[i] = {s.x + o[i].x, s.y - o[i].y};
  return c;
}

PieceState spawn_state(Piece p, const Ruleset& r) {
  PieceState s;
  s.x = (p == Piece::O) ? (r.width / 2 - 1) : (r.width / 2 - 2);
  s.y = r.visible_height + 1;  // pieces appear in the two rows just above the visible field
  s.rot = 0;
  return s;
}

const std::array<Cell, 5>& kick_table(Piece p, int from_rot, bool cw) {
  from_rot &= 3;
  if (p == Piece::O) return kNone;
  if (p == Piece::I) return cw ? kICw[from_rot] : kICcw[from_rot];
  return cw ? kJlstzCw[from_rot] : kJlstzCcw[from_rot];
}

// ---------------- Board ----------------

Board::Board(int width, int height) : w_(width), h_(height), rows_(static_cast<size_t>(height), 0u) {}

bool Board::filled(int x, int y) const {
  if (x < 0 || x >= w_ || y < 0 || y >= h_) return true;
  return (rows_[y] >> x) & 1u;
}

bool Board::collides(const Cells& c) const {
  for (const Cell& k : c)
    if (filled(k.x, k.y)) return true;
  return false;
}

void Board::place(const Cells& c) {
  for (const Cell& k : c)
    if (k.x >= 0 && k.x < w_ && k.y >= 0 && k.y < h_) rows_[k.y] |= (1u << k.x);
}

int Board::clear_lines() {
  const uint32_t full = full_mask();
  int write = 0, cleared = 0;
  for (int y = 0; y < h_; ++y) {
    if (rows_[y] == full) {
      ++cleared;
      continue;
    }
    rows_[write++] = rows_[y];
  }
  for (; write < h_; ++write) rows_[write] = 0u;
  return cleared;
}

int Board::column_height(int x) const {
  for (int y = h_ - 1; y >= 0; --y)
    if ((rows_[y] >> x) & 1u) return y + 1;
  return 0;
}

uint64_t Board::hash() const {
  uint64_t h = 1469598103934665603ull;
  for (uint32_t r : rows_) {
    for (int i = 0; i < 4; ++i) {
      h ^= (r >> (8 * i)) & 0xFFu;
      h *= 1099511628211ull;
    }
  }
  return h;
}

}  // namespace tbt
