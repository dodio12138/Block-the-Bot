/* Block the Bot core — C ABI for Python (ctypes) and other hosts.
 * Returned strings are owned by the game handle and stay valid until the next call on it. */
#ifndef TBT_CAPI_H
#define TBT_CAPI_H

#ifdef __cplusplus
extern "C" {
#endif

#if defined(_WIN32)
#define TBT_API __declspec(dllexport)
#else
#define TBT_API __attribute__((visibility("default")))
#endif

typedef struct tbt_game tbt_game;

/* piece ids: 0=I 1=O 2=T 3=S 4=Z 5=J 6=L ; mode: 0=free 1=bag7 ; topout: 0=lock_out 1=any_above */
TBT_API const char* tbt_version(void);
TBT_API tbt_game* tbt_game_create(int width, int visible_height, int hidden_rows, int mode, int hold, int topout);
TBT_API void tbt_game_destroy(tbt_game* g);
TBT_API tbt_game* tbt_game_clone(tbt_game* g); /* independent deep copy (for speculative look-ahead) */
TBT_API void tbt_game_reset(tbt_game* g);
TBT_API int tbt_game_set_rows(tbt_game* g, const unsigned int* rows_bottom_up, int n);
TBT_API int tbt_game_submit(tbt_game* g, int piece);
TBT_API int tbt_game_apply(tbt_game* g, int candidate_id); /* >=0 lines cleared, <0 error */
TBT_API int tbt_game_phase(tbt_game* g);                   /* 0 await_piece 1 await_action 2 game_over */
TBT_API int tbt_game_candidate_count(tbt_game* g);
TBT_API int tbt_game_allowed_mask(tbt_game* g);
TBT_API int tbt_game_best(tbt_game* g, const double* weights, int n, double* score_out);
/* writes evaluate() for every candidate (index = candidate id); returns count written */
TBT_API int tbt_game_scores(tbt_game* g, const double* weights, int n, double* out, int cap);
/* two-ply search teacher; values_out (cap entries, may be NULL) gets per-candidate values, NaN if unexplored */
TBT_API int tbt_game_teacher(tbt_game* g, const double* weights, int n, int top_n, double robust, double* values_out, int cap);
TBT_API int tbt_game_piece_pressure(tbt_game* g, const double* weights, int n, double* out7);
TBT_API const char* tbt_game_state_json(tbt_game* g);
TBT_API const char* tbt_game_candidates_json(tbt_game* g, int include_paths);
TBT_API const char* tbt_game_replay_text(tbt_game* g);
TBT_API int tbt_replay_verify(const char* text, char* hash_out17, char* err_out, int err_len);
TBT_API const char* tbt_status_name(int status);

#ifdef __cplusplus
}
#endif
#endif
