// Headless batch simulation and replay verification.
//   tbt_headless sim --games 50 --mode bag7 --attacker adversarial --seed 1 [--max-pieces 500]
//                    [--start-height 0] [--no-hold] [--replay-dir DIR] [--json]
//   tbt_headless replay FILE.tbr
#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstdio>
#include <cstring>
#include <fstream>
#include <sstream>
#include <string>
#include <vector>

#include "tbt/core.hpp"

using namespace tbt;

namespace {

enum class Attacker { Random, Bag, Adversarial, SZ };

Piece pick_piece(const Game& g, Attacker a, Rng& rng, const Weights& w) {
  const uint8_t mask = g.allowed_mask();
  std::vector<int> allowed;
  for (int p = 0; p < kPieceCount; ++p)
    if (mask & (1u << p)) allowed.push_back(p);
  switch (a) {
    case Attacker::Random:
    case Attacker::Bag:  // in bag7 mode the core already restricts to the remaining bag
      return static_cast<Piece>(allowed[rng.below(static_cast<int>(allowed.size()))]);
    case Attacker::SZ: {
      std::vector<int> sz;
      for (int p : allowed)
        if (p == pidx(Piece::S) || p == pidx(Piece::Z)) sz.push_back(p);
      const auto& from = sz.empty() ? allowed : sz;
      return static_cast<Piece>(from[rng.below(static_cast<int>(from.size()))]);
    }
    case Attacker::Adversarial: {
      const auto pr = piece_pressure(g, w);
      double worst = std::numeric_limits<double>::infinity();
      std::vector<int> ties;
      for (int p : allowed) {
        if (pr[p] < worst - 1e-9) {
          worst = pr[p];
          ties = {p};
        } else if (std::fabs(pr[p] - worst) <= 1e-9) {
          ties.push_back(p);
        }
      }
      return static_cast<Piece>(ties[rng.below(static_cast<int>(ties.size()))]);
    }
  }
  return Piece::T;
}

int cmd_sim(int argc, char** argv) {
  int games = 20, max_pieces = 500, start_height = 0;
  uint64_t seed = 1;
  Ruleset r;
  Attacker att = Attacker::Random;
  std::string replay_dir;
  bool json = false;
  for (int i = 2; i < argc; ++i) {
    std::string a = argv[i];
    auto next = [&]() -> std::string { return i + 1 < argc ? argv[++i] : ""; };
    if (a == "--games") games = std::stoi(next());
    else if (a == "--max-pieces") max_pieces = std::stoi(next());
    else if (a == "--seed") seed = std::stoull(next());
    else if (a == "--start-height") start_height = std::stoi(next());
    else if (a == "--no-hold") r.hold_enabled = false;
    else if (a == "--any-above") r.topout = TopOutRule::AnyAbove;
    else if (a == "--replay-dir") replay_dir = next();
    else if (a == "--json") json = true;
    else if (a == "--mode") r.mode = next() == "free" ? Mode::Free : Mode::Bag7;
    else if (a == "--attacker") {
      const std::string v = next();
      att = v == "adversarial" ? Attacker::Adversarial : v == "bag" ? Attacker::Bag : v == "sz" ? Attacker::SZ : Attacker::Random;
    } else {
      std::fprintf(stderr, "unknown arg %s\n", a.c_str());
      return 2;
    }
  }
  const Weights w;
  std::vector<int> survived;
  std::vector<int> lines;
  int topouts = 0;
  long long total_pieces = 0;
  const auto t0 = std::chrono::steady_clock::now();
  for (int gi = 0; gi < games; ++gi) {
    Rng rng(seed * 1000003ull + static_cast<uint64_t>(gi));
    Game g(r);
    if (start_height > 0) {
      std::vector<uint32_t> rows;
      for (int y = 0; y < start_height; ++y) rows.push_back(g.board().full_mask() & ~(1u << rng.below(r.width)));
      g.set_board(rows);
    }
    while (g.phase() != Phase::GameOver && g.pieces_submitted() < max_pieces) {
      g.submit(pick_piece(g, att, rng, w));
      if (g.phase() == Phase::GameOver) break;
      g.apply(best_candidate(g, w));
    }
    if (g.phase() == Phase::GameOver) ++topouts;
    survived.push_back(g.pieces_placed());
    lines.push_back(g.lines_total());
    total_pieces += g.pieces_submitted();
    if (!replay_dir.empty()) {
      std::ofstream f(replay_dir + "/sim_" + std::to_string(seed) + "_" + std::to_string(gi) + ".tbr");
      f << g.replay_text();
    }
  }
  const double secs = std::chrono::duration<double>(std::chrono::steady_clock::now() - t0).count();
  std::vector<int> sorted = survived;
  std::sort(sorted.begin(), sorted.end());
  double mean_s = 0, mean_l = 0;
  for (int v : survived) mean_s += v;
  for (int v : lines) mean_l += v;
  mean_s /= std::max(1, games);
  mean_l /= std::max(1, games);
  const int median = sorted.empty() ? 0 : sorted[sorted.size() / 2];
  const double ms_per_piece = total_pieces ? 1000.0 * secs / static_cast<double>(total_pieces) : 0.0;
  if (json) {
    std::printf("{\"games\":%d,\"mean_survived\":%.2f,\"median_survived\":%d,\"mean_lines\":%.2f,\"topout_rate\":%.3f,\"ms_per_piece\":%.3f}\n",
                games, mean_s, median, mean_l, static_cast<double>(topouts) / std::max(1, games), ms_per_piece);
  } else {
    std::printf("games=%d mean_survived=%.2f median_survived=%d mean_lines=%.2f topout_rate=%.3f ms_per_piece=%.3f\n",
                games, mean_s, median, mean_l, static_cast<double>(topouts) / std::max(1, games), ms_per_piece);
  }
  return 0;
}

int cmd_replay(int argc, char** argv) {
  if (argc < 3) {
    std::fprintf(stderr, "usage: tbt_headless replay FILE\n");
    return 2;
  }
  std::ifstream f(argv[2]);
  std::stringstream ss;
  ss << f.rdbuf();
  Game g;
  std::string err;
  const Status s = Game::replay(ss.str(), g, err);
  if (s != Status::Ok) {
    std::printf("replay FAILED: %s (%s)\n", status_name(s), err.c_str());
    return 1;
  }
  std::printf("replay OK pieces=%d placed=%d lines=%d game_over=%d hash=%016llx\n", g.pieces_submitted(),
              g.pieces_placed(), g.lines_total(), g.phase() == Phase::GameOver,
              static_cast<unsigned long long>(g.hash()));
  return 0;
}

}  // namespace

int main(int argc, char** argv) {
  if (argc >= 2 && std::strcmp(argv[1], "sim") == 0) return cmd_sim(argc, argv);
  if (argc >= 2 && std::strcmp(argv[1], "replay") == 0) return cmd_replay(argc, argv);
  std::printf("tbt_headless %s\n  sim [--games N] [--mode free|bag7] [--attacker random|bag|adversarial|sz] [--seed S]\n"
              "      [--max-pieces M] [--start-height H] [--no-hold] [--any-above] [--replay-dir DIR] [--json]\n"
              "  replay FILE.tbr\n",
              kVersion);
  return argc >= 2 ? 2 : 0;
}
