#include <algorithm>
#include <cmath>
#include <limits>
#include <sstream>

#include "json.hpp"
#include "tbt/core.hpp"

namespace tbt {

const char* status_name(Status s) {
  switch (s) {
    case Status::Ok: return "ok";
    case Status::ErrPhase: return "wrong_phase";
    case Status::ErrPieceNotAllowed: return "piece_not_allowed";
    case Status::ErrBadPiece: return "bad_piece";
    case Status::ErrBadCandidate: return "bad_candidate";
    case Status::ErrGameOver: return "game_over";
    case Status::ErrBadBoard: return "bad_board";
    case Status::ErrParse: return "parse_error";
    case Status::ErrMismatch: return "replay_mismatch";
  }
  return "unknown";
}

Game::Game(const Ruleset& r) : rules_(r) {
  if (!rules_.valid()) rules_ = Ruleset{};
  reset();
}

void Game::reset() {
  board_ = Board(rules_.width, rules_.total_height());
  phase_ = Phase::AwaitPiece;
  current_.reset();
  hold_.reset();
  candidates_.clear();
  bag_used_ = 0;
  pieces_submitted_ = pieces_placed_ = lines_total_ = last_lines_ = 0;
  ++version_;
  topout_reason_.clear();
  log_.clear();
  rebuild_header();
}

void Game::rebuild_header() {
  std::ostringstream h;
  h << "TBR 1\n";
  h << "rules " << rules_.width << ' ' << rules_.visible_height << ' ' << rules_.hidden_rows << ' '
    << (rules_.hold_enabled ? 1 : 0) << ' ' << static_cast<int>(rules_.topout) << ' '
    << static_cast<int>(rules_.mode) << '\n';
  h << "board";
  for (uint32_t row : board_.rows()) h << ' ' << std::hex << row << std::dec;
  h << '\n';
  header_ = h.str();
}

Status Game::set_board(const std::vector<uint32_t>& rows) {
  if (phase_ != Phase::AwaitPiece || pieces_submitted_ != 0) return Status::ErrPhase;
  if (static_cast<int>(rows.size()) > board_.height()) return Status::ErrBadBoard;
  for (int y = 0; y < board_.height(); ++y) board_.set_row(y, y < static_cast<int>(rows.size()) ? rows[y] : 0u);
  // A full row in the start board would be cleared immediately; reject it to keep replays simple.
  for (int y = 0; y < board_.height(); ++y)
    if (board_.row(y) == board_.full_mask()) return Status::ErrBadBoard;
  ++version_;
  rebuild_header();
  return Status::Ok;
}

uint8_t Game::allowed_mask() const {
  if (phase_ != Phase::AwaitPiece) return 0;
  if (rules_.mode == Mode::Free) return 0x7F;
  return static_cast<uint8_t>(~bag_used_ & 0x7F);
}

Status Game::submit(Piece p) {
  if (pidx(p) < 0 || pidx(p) >= kPieceCount) return Status::ErrBadPiece;
  if (phase_ == Phase::GameOver) return Status::ErrGameOver;
  if (phase_ != Phase::AwaitPiece) return Status::ErrPhase;
  const uint8_t bit = static_cast<uint8_t>(1u << pidx(p));
  if (!(allowed_mask() & bit)) return Status::ErrPieceNotAllowed;
  if (rules_.mode == Mode::Bag7) {
    bag_used_ |= bit;
    if (bag_used_ == 0x7F) bag_used_ = 0;
  }
  current_ = p;
  ++pieces_submitted_;
  ++version_;
  log_ += "P ";
  log_ += piece_char(p);
  log_ += '\n';
  generate_candidates();
  if (candidates_.empty()) {
    phase_ = Phase::GameOver;
    topout_reason_ = "block_out";
    return Status::Ok;
  }
  phase_ = Phase::AwaitAction;
  return Status::Ok;
}

void Game::generate_candidates() {
  candidates_.clear();
  std::vector<Candidate> own = search_placements(board_, rules_, *current_);
  if (own.empty()) return;  // spawn blocked: block out
  for (auto& c : own) candidates_.push_back(std::move(c));
  if (rules_.hold_enabled) {
    if (hold_) {
      std::vector<Candidate> swap = search_placements(board_, rules_, *hold_);
      for (auto& c : swap) {
        c.use_hold = true;
        candidates_.push_back(std::move(c));
      }
    } else {
      Candidate h;
      h.piece = *current_;
      h.hold_only = true;
      h.state = spawn_state(*current_, rules_);
      h.cells = piece_cells(*current_, h.state);
      h.features = compute_features(board_, rules_);
      h.features.landing_height = 0.0;
      candidates_.push_back(std::move(h));
    }
  }
  for (size_t i = 0; i < candidates_.size(); ++i) candidates_[i].id = static_cast<int>(i);
}

Status Game::apply(int id, int* lines_out) {
  if (phase_ == Phase::GameOver) return Status::ErrGameOver;
  if (phase_ != Phase::AwaitAction) return Status::ErrPhase;
  if (id < 0 || id >= static_cast<int>(candidates_.size())) return Status::ErrBadCandidate;
  const Candidate c = candidates_[id];
  {
    std::ostringstream a;
    a << "A " << id << ' ' << c.state.x << ' ' << c.state.y << ' ' << c.state.rot << ' '
      << (c.hold_only ? 2 : (c.use_hold ? 1 : 0)) << '\n';
    log_ += a.str();
  }
  int lines = 0;
  bool topout = false;
  if (c.hold_only) {
    hold_ = current_;
  } else {
    if (c.use_hold) hold_ = current_;
    bool above = true;
    for (const Cell& k : c.cells)
      if (k.y < rules_.visible_height) above = false;
    board_.place(c.cells);
    lines = board_.clear_lines();
    ++pieces_placed_;
    lines_total_ += lines;
    if (rules_.topout == TopOutRule::LockOut && above) {
      topout = true;
      topout_reason_ = "lock_out";
    }
    if (rules_.topout == TopOutRule::AnyAbove) {
      for (int y = rules_.visible_height; y < board_.height(); ++y)
        if (board_.row(y)) {
          topout = true;
          topout_reason_ = "above_visible";
        }
    }
  }
  last_lines_ = lines;
  current_.reset();
  candidates_.clear();
  ++version_;
  phase_ = topout ? Phase::GameOver : Phase::AwaitPiece;
  if (lines_out) *lines_out = lines;
  return Status::Ok;
}

uint64_t Game::hash() const {
  uint64_t h = board_.hash();
  auto mix = [&h](uint64_t v) {
    h ^= v + 0x9E3779B97F4A7C15ull + (h << 6) + (h >> 2);
  };
  mix(static_cast<uint64_t>(pieces_submitted_));
  mix(static_cast<uint64_t>(pieces_placed_));
  mix(static_cast<uint64_t>(lines_total_));
  mix(hold_ ? static_cast<uint64_t>(pidx(*hold_)) + 1 : 0);
  mix(static_cast<uint64_t>(phase_));
  return h;
}

namespace {

void write_features(Json& j, const Features& f, int width) {
  j.obj();
  j.key("heights").arr();
  for (int x = 0; x < width; ++x) j.i(f.heights[x]);
  j.end_arr();
  j.key("max").i(f.max_height);
  j.key("agg").i(f.aggregate_height);
  j.key("holes").i(f.holes);
  j.key("bump").i(f.bumpiness);
  j.key("wells").i(f.wells_sum);
  j.key("max_well").i(f.max_well);
  j.key("row_t").i(f.row_transitions);
  j.key("col_t").i(f.col_transitions);
  j.key("landing").d(f.landing_height);
  j.key("top_risk").d(f.top_risk);
  j.key("risk").d(f.risk);
  j.key("topout").b(f.topout);
  j.end_obj();
}

void write_cells(Json& j, const Cells& c) {
  j.arr();
  for (const Cell& k : c) j.arr().i(k.x).i(k.y).end_arr();
  j.end_arr();
}

std::string piece_str(std::optional<Piece> p) { return p ? std::string(1, piece_char(*p)) : std::string(); }

std::string mask_str(uint8_t m) {
  std::string s;
  for (int i = 0; i < kPieceCount; ++i)
    if (m & (1u << i)) s += piece_char(static_cast<Piece>(i));
  return s;
}

}  // namespace

std::string Game::state_json() const {
  Json j;
  j.obj();
  j.key("state_version").i(static_cast<long long>(version_));
  j.key("phase").str(phase_ == Phase::AwaitPiece ? "await_piece" : phase_ == Phase::AwaitAction ? "await_action" : "game_over");
  j.key("ruleset").obj();
  j.key("width").i(rules_.width);
  j.key("visible_height").i(rules_.visible_height);
  j.key("hidden_rows").i(rules_.hidden_rows);
  j.key("hold").b(rules_.hold_enabled);
  j.key("topout").str(rules_.topout == TopOutRule::LockOut ? "lock_out" : "any_above");
  j.key("mode").str(rules_.mode == Mode::Free ? "free" : "bag7");
  j.end_obj();
  j.key("rows").arr();
  for (uint32_t r : board_.rows()) j.i(r);
  j.end_arr();
  j.key("features");
  write_features(j, compute_features(board_, rules_), rules_.width);
  j.key("current").str(piece_str(current_));
  j.key("hold").str(piece_str(hold_));
  j.key("allowed").str(mask_str(allowed_mask()));
  j.key("bag_remaining").str(rules_.mode == Mode::Bag7 ? mask_str(static_cast<uint8_t>(~bag_used_ & 0x7F)) : "");
  j.key("pieces_submitted").i(pieces_submitted_);
  j.key("pieces_placed").i(pieces_placed_);
  j.key("lines_total").i(lines_total_);
  j.key("last_lines").i(last_lines_);
  j.key("candidate_count").i(static_cast<long long>(candidates_.size()));
  j.key("game_over").b(phase_ == Phase::GameOver);
  j.key("topout_reason").str(topout_reason_);
  {
    char buf[24];
    std::snprintf(buf, sizeof(buf), "%016llx", static_cast<unsigned long long>(hash()));
    j.key("hash").str(buf);
  }
  j.end_obj();
  return j.out();
}

std::string Game::candidates_json(bool include_paths) const {
  Json j;
  j.arr();
  for (const Candidate& c : candidates_) {
    j.obj();
    j.key("id").i(c.id);
    j.key("piece").str(std::string(1, piece_char(c.piece)));
    j.key("x").i(c.state.x);
    j.key("y").i(c.state.y);
    j.key("rot").i(c.state.rot);
    j.key("hold").b(c.use_hold || c.hold_only);
    j.key("hold_only").b(c.hold_only);
    j.key("lines").i(c.lines);
    j.key("tspin").b(c.tspin);
    j.key("cells");
    if (c.hold_only) j.arr().end_arr(); else write_cells(j, c.cells);
    j.key("f");
    write_features(j, c.features, rules_.width);
    if (include_paths) {
      j.key("path").arr();
      for (const PieceState& s : c.path) write_cells(j, piece_cells(c.piece, s));
      j.end_arr();
    }
    j.end_obj();
  }
  j.end_arr();
  return j.out();
}

Status Game::replay(const std::string& text, Game& out, std::string& err) {
  std::istringstream in(text);
  std::string line;
  while (std::getline(in, line) && (line.empty() || line[0] == '#')) {
  }
  if (line.rfind("TBR 1", 0) != 0) {
    err = "missing TBR header";
    return Status::ErrParse;
  }
  Ruleset r;
  std::vector<uint32_t> rows;
  bool have_rules = false;
  int step = 0;
  while (std::getline(in, line)) {
    if (line.empty() || line[0] == '#') continue;
    std::istringstream ls(line);
    std::string tag;
    ls >> tag;
    if (tag == "rules") {
      int hold = 1, topout = 0, mode = 1;
      ls >> r.width >> r.visible_height >> r.hidden_rows >> hold >> topout >> mode;
      r.hold_enabled = hold != 0;
      r.topout = static_cast<TopOutRule>(topout);
      r.mode = static_cast<Mode>(mode);
      if (!ls || !r.valid()) {
        err = "bad rules line";
        return Status::ErrParse;
      }
      have_rules = true;
    } else if (tag == "board") {
      std::string hex;
      while (ls >> hex) rows.push_back(static_cast<uint32_t>(std::stoul(hex, nullptr, 16)));
      if (!have_rules) {
        err = "board before rules";
        return Status::ErrParse;
      }
      out = Game(r);
      if (out.set_board(rows) != Status::Ok) {
        err = "bad board";
        return Status::ErrBadBoard;
      }
    } else if (tag == "P") {
      std::string pc;
      ls >> pc;
      Piece p;
      if (pc.empty() || !piece_from_char(pc[0], p)) {
        err = "bad piece at step " + std::to_string(step);
        return Status::ErrParse;
      }
      const Status s = out.submit(p);
      if (s != Status::Ok) {
        err = std::string("submit failed: ") + status_name(s) + " at step " + std::to_string(step);
        return s;
      }
      ++step;
    } else if (tag == "A") {
      int id, x, y, rot, hold;
      ls >> id >> x >> y >> rot >> hold;
      if (!ls) {
        err = "bad action line";
        return Status::ErrParse;
      }
      if (id < 0 || id >= static_cast<int>(out.candidates().size())) {
        err = "candidate id out of range at step " + std::to_string(step);
        return Status::ErrMismatch;
      }
      const Candidate& c = out.candidates()[id];
      const int h = c.hold_only ? 2 : (c.use_hold ? 1 : 0);
      if (c.state.x != x || c.state.y != y || c.state.rot != rot || h != hold) {
        err = "candidate mismatch at step " + std::to_string(step);
        return Status::ErrMismatch;
      }
      const Status s = out.apply(id);
      if (s != Status::Ok) {
        err = std::string("apply failed: ") + status_name(s);
        return s;
      }
    } else {
      err = "unknown tag " + tag;
      return Status::ErrParse;
    }
  }
  return Status::Ok;
}

// ---------------- Baseline policy ----------------

Weights Weights::from_array(const double* w, int n) {
  Weights out;
  if (!w) return out;
  double* fields[kCount] = {&out.landing, &out.lines, &out.row_t, &out.col_t,
                            &out.holes,   &out.wells, &out.bump,  &out.max_h};
  for (int i = 0; i < std::min(n, kCount); ++i) *fields[i] = w[i];
  return out;
}

double evaluate(const Candidate& c, const Weights& w) {
  const Features& f = c.features;
  if (f.topout) return -1e9;
  return w.landing * f.landing_height + w.lines * c.lines + w.row_t * f.row_transitions +
         w.col_t * f.col_transitions + w.holes * f.holes + w.wells * f.wells_sum + w.bump * f.bumpiness +
         w.max_h * f.max_height;
}

int best_candidate(const Game& g, const Weights& w, double* score_out) {
  int best = -1;
  double bs = -std::numeric_limits<double>::infinity();
  for (const Candidate& c : g.candidates()) {
    const double s = evaluate(c, w);
    if (s > bs) {
      bs = s;
      best = c.id;
    }
  }
  if (score_out) *score_out = bs;
  return best;
}

std::array<double, kPieceCount> piece_pressure(const Game& g, const Weights& w) {
  std::array<double, kPieceCount> out;
  out.fill(std::numeric_limits<double>::quiet_NaN());
  const uint8_t mask = g.allowed_mask();
  for (int p = 0; p < kPieceCount; ++p) {
    if (!(mask & (1u << p))) continue;
    Game copy = g;
    copy.submit(static_cast<Piece>(p));
    if (copy.phase() == Phase::GameOver) {
      out[p] = -1e9;
      continue;
    }
    double s = 0;
    best_candidate(copy, w, &s);
    out[p] = s;
  }
  return out;
}

}  // namespace tbt
