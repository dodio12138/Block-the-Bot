// Core rule tests (no external framework).
#include <cmath>
#include <cstdio>
#include <set>
#include <string>

#include "tbt/core.hpp"

using namespace tbt;

static int g_failures = 0;
#define CHECK(cond)                                                     \
  do {                                                                  \
    if (!(cond)) {                                                      \
      std::printf("  FAIL %s:%d: %s\n", __FILE__, __LINE__, #cond);    \
      ++g_failures;                                                     \
    }                                                                   \
  } while (0)

static Ruleset rules(Mode m = Mode::Free, bool hold = false) {
  Ruleset r;
  r.mode = m;
  r.hold_enabled = hold;
  return r;
}

static void test_shapes() {
  for (int p = 0; p < kPieceCount; ++p) {
    for (int rot = 0; rot < 4; ++rot) {
      std::set<std::pair<int, int>> s;
      for (const Cell& c : shape_offsets(static_cast<Piece>(p), rot)) {
        s.insert({c.x, c.y});
        CHECK(c.x >= 0 && c.x < box_size(static_cast<Piece>(p)));
        CHECK(c.y >= 0 && c.y < box_size(static_cast<Piece>(p)));
      }
      CHECK(s.size() == 4);
    }
  }
}

static void test_kick_symmetry() {
  for (int p = 0; p < kPieceCount; ++p) {
    for (int from = 0; from < 4; ++from) {
      const auto& cw = kick_table(static_cast<Piece>(p), from, true);
      const auto& back = kick_table(static_cast<Piece>(p), (from + 1) & 3, false);
      for (int i = 0; i < 5; ++i) {
        CHECK(cw[i].x == -back[i].x);
        CHECK(cw[i].y == -back[i].y);
      }
    }
  }
}

static void test_empty_board_counts() {
  const Ruleset r = rules();
  const Board b(r.width, r.total_height());
  const int expected[kPieceCount] = {17, 9, 34, 17, 17, 34, 34};  // I O T S Z J L
  for (int p = 0; p < kPieceCount; ++p) {
    const auto c = search_placements(b, r, static_cast<Piece>(p));
    if (static_cast<int>(c.size()) != expected[p])
      std::printf("  piece %c: %zu placements (expected %d)\n", piece_char(static_cast<Piece>(p)), c.size(), expected[p]);
    CHECK(static_cast<int>(c.size()) == expected[p]);
    for (const auto& k : c) {
      int min_y = 99;
      for (const Cell& cell : k.cells) min_y = std::min(min_y, cell.y);
      CHECK(min_y == 0);  // everything rests on the floor
    }
  }
}

static bool path_is_legal(const Board& b, const Candidate& c) {
  for (size_t i = 0; i < c.path.size(); ++i) {
    if (b.collides(piece_cells(c.piece, c.path[i]))) return false;
    if (i == 0) continue;
    const PieceState& a = c.path[i - 1];
    const PieceState& n = c.path[i];
    const int dx = n.x - a.x, dy = n.y - a.y;
    if (n.rot == a.rot) {
      if (!((std::abs(dx) == 1 && dy == 0) || (dx == 0 && dy == -1))) return false;
    } else {
      const bool cw = n.rot == ((a.rot + 1) & 3);
      if (!cw && n.rot != ((a.rot + 3) & 3)) return false;
      bool found = false;
      for (const Cell& k : kick_table(c.piece, a.rot, cw)) {
        if (!b.collides(piece_cells(c.piece, {a.x + k.x, a.y + k.y, n.rot}))) {
          found = (k.x == dx && k.y == dy);  // must be the FIRST free kick
          break;
        }
      }
      if (!found) return false;
    }
  }
  PieceState below = c.path.back();
  below.y -= 1;
  return b.collides(piece_cells(c.piece, below)) && c.path.back() == c.state;
}

static void test_tspin_double() {
  const Ruleset r = rules();
  Board b(r.width, r.total_height());
  b.set_row(0, b.full_mask() & ~(1u << 4));
  b.set_row(1, b.full_mask() & ~(7u << 3));
  b.set_row(2, 0xFu);
  const auto cands = search_placements(b, r, Piece::T);
  bool found = false;
  for (const auto& c : cands) {
    CHECK(path_is_legal(b, c));
    if (c.lines == 2) {
      found = true;
      CHECK(c.tspin);
      CHECK(c.state.rot == 2);
    }
  }
  CHECK(found);
}

static void test_paths_random_boards() {
  const Ruleset r = rules();
  Rng rng(42);
  int checked = 0;
  for (int t = 0; t < 40; ++t) {
    Board b(r.width, r.total_height());
    const int h = 2 + rng.below(10);
    for (int y = 0; y < h; ++y) b.set_row(y, static_cast<uint32_t>(rng.next()) & b.full_mask() & ~(1u << rng.below(10)));
    for (int p = 0; p < kPieceCount; ++p) {
      for (const auto& c : search_placements(b, r, static_cast<Piece>(p))) {
        CHECK(path_is_legal(b, c));
        ++checked;
      }
    }
  }
  CHECK(checked > 1000);
}

static void test_line_clear_and_bag() {
  Game g(rules(Mode::Bag7));
  std::vector<uint32_t> rows = {0x3FFu & ~0xFu};  // cols 4..9 filled
  CHECK(g.set_board(rows) == Status::Ok);
  CHECK(g.submit(Piece::I) == Status::Ok);
  int id = -1;
  for (const auto& c : g.candidates())
    if (c.lines == 1) id = c.id;
  CHECK(id >= 0);
  int lines = -1;
  CHECK(g.apply(id, &lines) == Status::Ok);
  CHECK(lines == 1);
  CHECK(g.board().row(0) == 0u);
  CHECK(g.submit(Piece::I) == Status::ErrPieceNotAllowed);  // already used in this bag
  const Piece rest[] = {Piece::O, Piece::T, Piece::S, Piece::Z, Piece::J, Piece::L};
  for (Piece p : rest) {
    CHECK(g.submit(p) == Status::Ok);
    CHECK(g.apply(best_candidate(g, Weights{})) == Status::Ok);
  }
  CHECK(g.allowed_mask() == 0x7F);  // new bag
}

static void test_hold() {
  Game g(rules(Mode::Free, true));
  CHECK(g.submit(Piece::I) == Status::Ok);
  int hold_id = -1;
  for (const auto& c : g.candidates())
    if (c.hold_only) hold_id = c.id;
  CHECK(hold_id >= 0);
  CHECK(g.apply(hold_id) == Status::Ok);
  CHECK(g.hold() && *g.hold() == Piece::I);
  CHECK(g.pieces_placed() == 0);
  CHECK(g.submit(Piece::O) == Status::Ok);
  int swap_id = -1;
  for (const auto& c : g.candidates()) {
    CHECK(!c.hold_only);
    if (c.use_hold && swap_id < 0) swap_id = c.id;
  }
  CHECK(swap_id >= 0);
  CHECK(g.candidates()[swap_id].piece == Piece::I);
  CHECK(g.apply(swap_id) == Status::Ok);
  CHECK(g.hold() && *g.hold() == Piece::O);
}

static void test_block_out() {
  Game g(rules());
  std::vector<uint32_t> rows;
  for (int y = 0; y < 22; ++y) rows.push_back(y % 2 ? 0x1FFu : 0x3FEu);
  CHECK(g.set_board(rows) == Status::Ok);
  CHECK(g.submit(Piece::T) == Status::Ok);
  CHECK(g.phase() == Phase::GameOver);
  CHECK(g.topout_reason() == "block_out");
  CHECK(g.submit(Piece::T) == Status::ErrGameOver);
}

static uint64_t play(uint64_t seed, std::string* replay) {
  Game g(rules(Mode::Bag7, true));
  Rng rng(seed);
  const Weights w;
  while (g.phase() != Phase::GameOver && g.pieces_submitted() < 150) {
    const uint8_t m = g.allowed_mask();
    int p;
    do p = rng.below(kPieceCount); while (!(m & (1u << p)));
    g.submit(static_cast<Piece>(p));
    if (g.phase() == Phase::GameOver) break;
    g.apply(best_candidate(g, w));
  }
  if (replay) *replay = g.replay_text();
  return g.hash();
}

static void test_determinism_and_replay() {
  std::string rep;
  const uint64_t h1 = play(7, &rep);
  const uint64_t h2 = play(7, nullptr);
  CHECK(h1 == h2);
  Game out;
  std::string err;
  CHECK(Game::replay(rep, out, err) == Status::Ok);
  CHECK(out.hash() == h1);
  // Tampered replay must be rejected.
  std::string bad = rep;
  const size_t pos = bad.find("\nA ");
  CHECK(pos != std::string::npos);
  bad.replace(pos, 4, "\nA 9");
  Game out2;
  CHECK(Game::replay(bad, out2, err) != Status::Ok || out2.hash() != h1);
}

static void test_teacher() {
  Game g(rules(Mode::Bag7, true));
  Rng rng(3);
  const Weights w;
  for (int t = 0; t < 25 && g.phase() != Phase::GameOver; ++t) {
    const uint8_t m = g.allowed_mask();
    int p;
    do p = rng.below(kPieceCount); while (!(m & (1u << p)));
    g.submit(static_cast<Piece>(p));
    TeacherParams one;
    one.top_n = 1;
    CHECK(teacher_candidate(g, w, one) == best_candidate(g, w));
    std::vector<double> vals;
    const int a = teacher_candidate(g, w, TeacherParams{}, nullptr, &vals);
    const int b = teacher_candidate(g, w, TeacherParams{});
    CHECK(a == b);  // deterministic
    CHECK(a >= 0 && a < static_cast<int>(g.candidates().size()));
    CHECK(vals.size() == g.candidates().size() && !std::isnan(vals[a]));
    g.apply(a);
  }
}

int main() {
  struct T { const char* name; void (*fn)(); } tests[] = {
      {"shapes", test_shapes},
      {"kick_symmetry", test_kick_symmetry},
      {"empty_board_counts", test_empty_board_counts},
      {"tspin_double", test_tspin_double},
      {"paths_random_boards", test_paths_random_boards},
      {"line_clear_and_bag", test_line_clear_and_bag},
      {"hold", test_hold},
      {"block_out", test_block_out},
      {"determinism_and_replay", test_determinism_and_replay},
      {"teacher", test_teacher},
  };
  for (const auto& t : tests) {
    const int before = g_failures;
    t.fn();
    std::printf("[%s] %s\n", g_failures == before ? " OK " : "FAIL", t.name);
  }
  std::printf("%s (%d failures)\n", g_failures ? "FAILED" : "ALL PASSED", g_failures);
  return g_failures ? 1 : 0;
}
