#include "tbt/capi.h"

#include <algorithm>
#include <cstdio>
#include <cstring>
#include <string>

#include "tbt/core.hpp"

struct tbt_game {
  tbt::Game game;
  std::string buf;
};

using namespace tbt;

extern "C" {

const char* tbt_version(void) { return kVersion; }

tbt_game* tbt_game_create(int width, int visible_height, int hidden_rows, int mode, int hold, int topout) {
  Ruleset r;
  r.width = width;
  r.visible_height = visible_height;
  r.hidden_rows = hidden_rows;
  r.mode = mode == 0 ? Mode::Free : Mode::Bag7;
  r.hold_enabled = hold != 0;
  r.topout = topout == 1 ? TopOutRule::AnyAbove : TopOutRule::LockOut;
  if (!r.valid()) return nullptr;
  auto* g = new tbt_game{Game(r), {}};
  return g;
}

void tbt_game_destroy(tbt_game* g) { delete g; }

void tbt_game_reset(tbt_game* g) { g->game.reset(); }

int tbt_game_set_rows(tbt_game* g, const unsigned int* rows, int n) {
  std::vector<uint32_t> v(rows, rows + (n > 0 ? n : 0));
  return static_cast<int>(g->game.set_board(v));
}

int tbt_game_submit(tbt_game* g, int piece) {
  if (piece < 0 || piece >= kPieceCount) return static_cast<int>(Status::ErrBadPiece);
  return static_cast<int>(g->game.submit(static_cast<Piece>(piece)));
}

int tbt_game_apply(tbt_game* g, int id) {
  int lines = 0;
  const Status s = g->game.apply(id, &lines);
  return s == Status::Ok ? lines : static_cast<int>(s);
}

int tbt_game_phase(tbt_game* g) { return static_cast<int>(g->game.phase()); }

int tbt_game_candidate_count(tbt_game* g) { return static_cast<int>(g->game.candidates().size()); }

int tbt_game_allowed_mask(tbt_game* g) { return g->game.allowed_mask(); }

int tbt_game_best(tbt_game* g, const double* weights, int n, double* score_out) {
  return best_candidate(g->game, Weights::from_array(weights, n), score_out);
}

int tbt_game_scores(tbt_game* g, const double* weights, int n, double* out, int cap) {
  const Weights w = Weights::from_array(weights, n);
  const auto& c = g->game.candidates();
  const int count = std::min(cap, static_cast<int>(c.size()));
  for (int i = 0; i < count; ++i) out[i] = evaluate(c[i], w);
  return count;
}

int tbt_game_teacher(tbt_game* g, const double* weights, int n, int top_n, double robust, double* values_out, int cap) {
  TeacherParams tp;
  tp.top_n = top_n;
  tp.robust = robust;
  std::vector<double> vals;
  const int id = teacher_candidate(g->game, Weights::from_array(weights, n), tp, nullptr, values_out ? &vals : nullptr);
  if (values_out)
    for (int i = 0; i < std::min(cap, static_cast<int>(vals.size())); ++i) values_out[i] = vals[i];
  return id;
}

int tbt_game_piece_pressure(tbt_game* g, const double* weights, int n, double* out7) {
  const auto p = piece_pressure(g->game, Weights::from_array(weights, n));
  for (int i = 0; i < kPieceCount; ++i) out7[i] = p[i];
  return 0;
}

const char* tbt_game_state_json(tbt_game* g) {
  g->buf = g->game.state_json();
  return g->buf.c_str();
}

const char* tbt_game_candidates_json(tbt_game* g, int include_paths) {
  g->buf = g->game.candidates_json(include_paths != 0);
  return g->buf.c_str();
}

const char* tbt_game_replay_text(tbt_game* g) {
  g->buf = g->game.replay_text();
  return g->buf.c_str();
}

int tbt_replay_verify(const char* text, char* hash_out17, char* err_out, int err_len) {
  Game out;
  std::string err;
  const Status s = Game::replay(text ? text : "", out, err);
  if (hash_out17) std::snprintf(hash_out17, 17, "%016llx", static_cast<unsigned long long>(out.hash()));
  if (err_out && err_len > 0) {
    std::strncpy(err_out, err.c_str(), static_cast<size_t>(err_len - 1));
    err_out[err_len - 1] = '\0';
  }
  return static_cast<int>(s);
}

const char* tbt_status_name(int status) { return status_name(static_cast<Status>(status)); }

}  // extern "C"
