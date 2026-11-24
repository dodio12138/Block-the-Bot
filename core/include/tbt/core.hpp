// Block the Bot — deterministic Tetris rules core.
// Board convention: y = 0 is the bottom row, y grows upward. row[y] bit x = cell (x, y).
#pragma once

#include <array>
#include <cstdint>
#include <optional>
#include <string>
#include <vector>

namespace tbt {

constexpr int kMaxWidth = 16;
constexpr int kMaxHeight = 60;
constexpr int kPieceCount = 7;
constexpr const char* kVersion = "0.1.0";

enum class Piece : int { I = 0, O = 1, T = 2, S = 3, Z = 4, J = 5, L = 6 };
char piece_char(Piece p);
bool piece_from_char(char c, Piece& out);
inline int pidx(Piece p) { return static_cast<int>(p); }

enum class Mode : int { Free = 0, Bag7 = 1 };
enum class TopOutRule : int { LockOut = 0, AnyAbove = 1 };

struct Ruleset {
  int width = 10;
  int visible_height = 20;
  int hidden_rows = 4;
  bool hold_enabled = true;
  TopOutRule topout = TopOutRule::LockOut;
  Mode mode = Mode::Bag7;
  int total_height() const { return visible_height + hidden_rows; }
  bool valid() const;
};

struct Cell {
  int x = 0, y = 0;
  bool operator==(const Cell& o) const { return x == o.x && y == o.y; }
};
using Cells = std::array<Cell, 4>;

// Piece position: (x, y) is the top-left corner of the SRS bounding box in board
// coordinates (y = top row of the box). rot: 0 = spawn, 1 = R, 2 = 180, 3 = L.
struct PieceState {
  int x = 0, y = 0, rot = 0;
  bool operator==(const PieceState& o) const { return x == o.x && y == o.y && rot == o.rot; }
};

// Shape offsets in y-down box coordinates (cx, cy).
const Cells& shape_offsets(Piece p, int rot);
int box_size(Piece p);
Cells piece_cells(Piece p, const PieceState& s);
PieceState spawn_state(Piece p, const Ruleset& r);
// SRS kick offsets (dx, dy) with +y up, for rotating from `from_rot` clockwise or counter-clockwise.
const std::array<Cell, 5>& kick_table(Piece p, int from_rot, bool cw);

class Board {
 public:
  Board() : Board(10, 24) {}
  Board(int width, int height);
  int width() const { return w_; }
  int height() const { return h_; }
  // Out-of-bounds (walls, floor, ceiling) counts as filled.
  bool filled(int x, int y) const;
  uint32_t row(int y) const { return rows_[y]; }
  void set_row(int y, uint32_t bits) { rows_[y] = bits & full_mask(); }
  uint32_t full_mask() const { return (1u << w_) - 1u; }
  bool collides(const Cells& c) const;
  void place(const Cells& c);
  int clear_lines();
  int column_height(int x) const;
  uint64_t hash() const;
  const std::vector<uint32_t>& rows() const { return rows_; }

 private:
  int w_, h_;
  std::vector<uint32_t> rows_;
};

struct Features {
  std::array<int, kMaxWidth> heights{};
  int max_height = 0;
  int aggregate_height = 0;
  int holes = 0;
  int bumpiness = 0;
  int wells_sum = 0;  // cumulative well depth (1 + 2 + ... + d per well)
  int max_well = 0;   // deepest surface well
  int row_transitions = 0;
  int col_transitions = 0;
  double landing_height = 0.0;
  double top_risk = 0.0;  // 0..1, how close the stack is to the spawn zone
  double risk = 0.0;      // 0..1 combined danger score
  bool topout = false;
};
Features compute_features(const Board& b, const Ruleset& r);

struct Candidate {
  int id = -1;
  Piece piece = Piece::T;
  PieceState state;
  bool use_hold = false;   // places the held piece, current piece goes to hold
  bool hold_only = false;  // stores the current piece in an empty hold, nothing placed
  Cells cells{};
  std::vector<PieceState> path;  // spawn ... final state, each step one legal move
  int lines = 0;
  bool tspin = false;
  Features features;  // board after placement and line clears
};

// All distinct reachable lock positions for `p` (BFS over left/right/soft-drop/CW/CCW with SRS kicks).
// Empty result means the spawn position is blocked.
std::vector<Candidate> search_placements(const Board& b, const Ruleset& r, Piece p);

enum class Phase : int { AwaitPiece = 0, AwaitAction = 1, GameOver = 2 };
enum class Status : int {
  Ok = 0,
  ErrPhase = -1,
  ErrPieceNotAllowed = -2,
  ErrBadPiece = -3,
  ErrBadCandidate = -4,
  ErrGameOver = -5,
  ErrBadBoard = -6,
  ErrParse = -7,
  ErrMismatch = -8,
};
const char* status_name(Status s);

class Game {
 public:
  explicit Game(const Ruleset& r = Ruleset{});
  void reset();
  Status set_board(const std::vector<uint32_t>& rows_bottom_up);
  Status submit(Piece p);
  Status apply(int candidate_id, int* lines_out = nullptr);

  const Ruleset& rules() const { return rules_; }
  const Board& board() const { return board_; }
  Phase phase() const { return phase_; }
  const std::vector<Candidate>& candidates() const { return candidates_; }
  std::optional<Piece> current() const { return current_; }
  std::optional<Piece> hold() const { return hold_; }
  uint8_t allowed_mask() const;
  uint8_t bag_used() const { return bag_used_; }
  int pieces_submitted() const { return pieces_submitted_; }
  int pieces_placed() const { return pieces_placed_; }
  int lines_total() const { return lines_total_; }
  int last_lines() const { return last_lines_; }
  uint64_t state_version() const { return version_; }
  const std::string& topout_reason() const { return topout_reason_; }
  uint64_t hash() const;

  std::string state_json() const;
  std::string candidates_json(bool include_paths) const;
  std::string replay_text() const { return header_ + log_; }

  // Re-run a replay. Verifies every recorded action. Returns Ok and fills `out` on success.
  static Status replay(const std::string& text, Game& out, std::string& err);

 private:
  void generate_candidates();
  void rebuild_header();

  Ruleset rules_;
  Board board_;
  Phase phase_ = Phase::AwaitPiece;
  std::optional<Piece> current_, hold_;
  std::vector<Candidate> candidates_;
  uint8_t bag_used_ = 0;
  int pieces_submitted_ = 0, pieces_placed_ = 0, lines_total_ = 0, last_lines_ = 0;
  uint64_t version_ = 0;
  std::string topout_reason_;
  std::string header_, log_;
};

// ---- Baseline policy (traditional heuristic AI, also used as fallback) ----
struct Weights {
  // Dellacherie / El-Tetris style defaults.
  double landing = -4.500158825082766;
  double lines = 3.4181268101392694;
  double row_t = -3.2178882868487753;
  double col_t = -9.348695305445199;
  double holes = -7.899265427351652;
  double wells = -3.3855972247263626;
  double bump = 0.0;
  double max_h = 0.0;
  static constexpr int kCount = 8;
  static Weights from_array(const double* w, int n);
};
double evaluate(const Candidate& c, const Weights& w);
int best_candidate(const Game& g, const Weights& w, double* score_out = nullptr);
// Best achievable score for the defender if piece p were submitted next (NaN if not allowed).
std::array<double, kPieceCount> piece_pressure(const Game& g, const Weights& w);

struct Rng {
  uint64_t s;
  explicit Rng(uint64_t seed) : s(seed ? seed : 0x9E3779B97F4A7C15ull) {}
  uint64_t next() {
    uint64_t z = (s += 0x9E3779B97F4A7C15ull);
    z = (z ^ (z >> 30)) * 0xBF58476D1CE4E5B9ull;
    z = (z ^ (z >> 27)) * 0x94D049BB133111EBull;
    return z ^ (z >> 31);
  }
  int below(int n) { return static_cast<int>(next() % static_cast<uint64_t>(n)); }
};

}  // namespace tbt
