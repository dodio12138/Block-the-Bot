// tbt_ai_worker：游戏内嵌的本地推理进程（复制改编自 FromLev ai/worker/main.cpp）。
// 和 Agent 服务分开的独立进程，崩了游戏照常运行（Agent 会回退到启发式）。
// 只做推理：收「消息列表 + 可选的约束（GBNF 语法或 JSON Schema）」，回生成的文本。
//
// 协议：stdin / stdout 每行一条 JSON。
//   ← {"type":"hello","proto":"tbt-ai","version":1,"ok":true,"backend":"llama","model":"Qwen3-0.6B-Q8_0.gguf + bot-lora-v1.gguf"}
//   → {"type":"generate","id":3,"messages":[{"role","content"}],"grammar":"root ::= ...","format":{schema}?,"max_tokens":8,"temperature":0}
//   ← {"type":"token","id":3,"text":"…"}（流式片段）  ← {"type":"done","id":3,"text":"全文","ms":812,"tokens":2,"prompt_tokens":300}
//   ← {"type":"error","id":3,"error":"…"}
//   → {"type":"ping","id":n}  ← {"type":"pong","id":n}       → {"type":"cancel","id":3}   → {"type":"shutdown"}
//
// 后端：
//   mock    确定性假模型（测试用）：有 grammar 时回 "0"；提示词含 OUT=JSON 时回一个策略 JSON
//   llama   进程内 llama.cpp（b11700，静态链接，只用 CPU），--model=<.gguf> [--lora=<.gguf>]；
//           同一开头（系统提示词）的 KV 缓存留着复用
//
// 用法：tbt_ai_worker --backend=llama --model=game/models/xxx.gguf [--lora=game/models/bot-lora.gguf] [--threads=4] [--ctx=2048]
//       tbt_ai_worker --backend=mock [--mock-delay=毫秒]
#include <algorithm>
#include <atomic>
#include <chrono>
#include <condition_variable>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <deque>
#include <functional>
#include <iostream>
#include <map>
#include <memory>
#include <mutex>
#include <string>
#include <thread>

#include "json.hpp"

#ifdef TBT_WITH_LLAMA
#include "llama.h"
#endif

#ifndef _WIN32
#include <netdb.h>
#include <sys/resource.h>
#include <sys/socket.h>
#include <sys/time.h>
#include <unistd.h>
#endif

using tbtai::Json;

namespace {

std::mutex g_out_m;
void emit(const Json &j) {
    std::lock_guard<std::mutex> lk(g_out_m);
    std::string s = j.dump();
    s += '\n';
    std::fwrite(s.data(), 1, s.size(), stdout);
    std::fflush(stdout);
}

double now_ms() {
    using namespace std::chrono;
    return duration<double, std::milli>(steady_clock::now().time_since_epoch()).count();
}

using TokenFn = std::function<bool(const std::string &)>;   // 返回 false = 取消

class Backend {
public:
    virtual ~Backend() = default;
    virtual const char *name() const = 0;
    virtual std::string model() const = 0;
    virtual bool init(std::string &err) = 0;
    virtual bool generate(const Json &req, const TokenFn &tok, std::string &text, std::string &err, Json &stats) = 0;
};

std::string last_user(const Json &req) {
    std::string s;
    for (const auto &m : req.get("messages").elems()) if (m.get("role").str() == "user") s = m.get("content").str();
    return s;
}

// ---------- mock ----------
class MockBackend : public Backend {
public:
    int delay_ms = 0, crash_after = -1;
    bool hang = false;
    int count = 0;
    // （FromLev 的 mock 按「MOCK:」「事实：」回文本；这里改成本游戏的候选 ID / 策略 JSON）
    const char *name() const override { return "mock"; }
    std::string model() const override { return "mock"; }
    bool init(std::string &) override { return true; }
    bool generate(const Json &req, const TokenFn &tok, std::string &text, std::string &err, Json &stats) override {
        if (crash_after >= 0 && ++count > crash_after) std::_Exit(3);
        std::string u = last_user(req);
        if (u.find("OUT=JSON") != std::string::npos) text = "{\"strategy\":\"balanced\",\"valid_for\":5}";
        else text = "0";
        double t0 = now_ms();
        while (hang || now_ms() - t0 < delay_ms) {
            if (!tok("")) { err = "cancelled"; return false; }
            std::this_thread::sleep_for(std::chrono::milliseconds(5));
        }
        if (!tok(text)) { err = "cancelled"; return false; }
        stats["tokens"] = int(text.size() / 3);
        return true;
    }
};

#ifdef TBT_WITH_LLAMA
// ---------- llama.cpp（进程内推理，发行用） ----------
void llama_quiet(ggml_log_level level, const char *text, void *) {
    if (level >= GGML_LOG_LEVEL_ERROR) std::fputs(text, stderr);   // 只把错误打到 stderr：stdout 是协议通道
}

class LlamaBackend : public Backend {
public:
    std::string path, lora;   // lora：微调出的 LoRA 适配器（GGUF，ai/train 训练、convert_lora_to_gguf.py 转换），加载在底模上
    int threads = 4, n_ctx = 2048;
    ~LlamaBackend() override {
        if (ctx_) llama_free(ctx_);
        if (mdl_) llama_model_free(mdl_);
    }
    const char *name() const override { return "llama"; }
    std::string model() const override {
        auto base = [](const std::string &f) { size_t p = f.find_last_of("/\\"); return p == std::string::npos ? f : f.substr(p + 1); };
        return lora.empty() ? base(path) : base(path) + " + " + base(lora);
    }
    bool init(std::string &err) override {
        llama_log_set(llama_quiet, nullptr);
        llama_backend_init();
        if (path.empty()) { err = "没有指定模型文件（--model=<.gguf 路径>）"; return false; }
        auto mp = llama_model_default_params();
        mp.n_gpu_layers = 0;   // 只用 CPU（见 ai/CMakeLists.txt）
        mp.progress_callback = [](float, void *) { return true; };   // 不打加载进度点
        mdl_ = llama_model_load_from_file(path.c_str(), mp);
        if (!mdl_) { err = "加载模型失败：" + path; return false; }
        vocab_ = llama_model_get_vocab(mdl_);
        auto cp = llama_context_default_params();
        cp.n_ctx = uint32_t(n_ctx);
        cp.n_batch = uint32_t(n_ctx);
        cp.n_threads = threads;
        cp.n_threads_batch = threads;
        cp.no_perf = true;
        ctx_ = llama_init_from_model(mdl_, cp);
        if (!ctx_) { err = "建立推理上下文失败"; return false; }
        if (!lora.empty()) {
            llama_adapter_lora *ad = llama_adapter_lora_init(mdl_, lora.c_str());
            if (!ad) { err = "加载 LoRA 适配器失败：" + lora; return false; }
            float scale = 1.0f;
            if (llama_set_adapters_lora(ctx_, &ad, 1, &scale) != 0) { err = "LoRA 适配器和模型对不上：" + lora; return false; }
        }
        const char *t = llama_model_chat_template(mdl_, nullptr);
        tmpl_ = t ? t : "chatml";
        // 带思考链的模板（Qwen3）：在助手开头放一个空的思考块 = 非思考模式（和官方模板 enable_thinking=false 一样）
        think_ = tmpl_.find("<think>") != std::string::npos;
        return true;
    }
    bool generate(const Json &req, const TokenFn &tok, std::string &text, std::string &err, Json &stats) override {
        // 提示词：模型自带的对话模板
        std::vector<std::string> roles, contents;
        for (const auto &m : req.get("messages").elems()) { roles.push_back(m.get("role").str()); contents.push_back(m.get("content").str()); }
        std::vector<llama_chat_message> msgs;
        size_t total = 0;
        for (size_t i = 0; i < roles.size(); i++) { msgs.push_back({roles[i].c_str(), contents[i].c_str()}); total += contents[i].size(); }
        std::vector<char> buf(total * 2 + 1024);
        int n = llama_chat_apply_template(tmpl_.c_str(), msgs.data(), msgs.size(), true, buf.data(), int32_t(buf.size()));
        if (n > int(buf.size())) { buf.resize(size_t(n)); n = llama_chat_apply_template(tmpl_.c_str(), msgs.data(), msgs.size(), true, buf.data(), int32_t(buf.size())); }
        if (n < 0) { err = "不认识这个模型的对话模板"; return false; }
        std::string prompt(buf.data(), size_t(n));
        if (think_) prompt += "<think>\n\n</think>\n\n";
        int nt = -llama_tokenize(vocab_, prompt.c_str(), int32_t(prompt.size()), nullptr, 0, true, true);
        std::vector<llama_token> toks(size_t(std::max(nt, 0)));
        if (nt <= 0 || llama_tokenize(vocab_, prompt.c_str(), int32_t(prompt.size()), toks.data(), nt, true, true) < 0) { err = "分词失败"; return false; }
        int max_t = int(req.get("max_tokens").num(96));
        if (nt + max_t > n_ctx) { err = "提示词太长"; return false; }
        // 前缀缓存：和上一次相同的开头（系统提示词）不再重算
        size_t k = 0;
        while (k < cached_.size() && k < toks.size() && cached_[k] == toks[k]) k++;
        if (k == toks.size()) k--;   // 至少重算最后一个，才有 logits
        llama_memory_t mem = llama_get_memory(ctx_);
        if (!llama_memory_seq_rm(mem, 0, llama_pos(k), -1)) { llama_memory_clear(mem, true); k = 0; }
        cached_.resize(k);
        std::vector<llama_token> rest(toks.begin() + long(k), toks.end());
        if (llama_decode(ctx_, llama_batch_get_one(rest.data(), int32_t(rest.size()))) != 0) {
            llama_memory_clear(mem, true);
            cached_.clear();
            err = "推理失败";
            return false;
        }
        cached_.insert(cached_.end(), rest.begin(), rest.end());
        stats["prompt_tokens"] = int(toks.size());     // 整个提示词
        stats["prompt_computed"] = int(rest.size());   // 前缀缓存之外实际算的部分
        // 采样：有 Schema 就加语法约束；温度 0 = 贪心
        llama_sampler *sm = llama_sampler_chain_init(llama_sampler_chain_default_params());
        if (req.get("grammar").is_str() && !req.get("grammar").str().empty()) {
            llama_sampler *g = llama_sampler_init_grammar(vocab_, req.get("grammar").str().c_str(), "root");
            if (!g) { llama_sampler_free(sm); err = "约束语法无效"; return false; }
            llama_sampler_chain_add(sm, g);
        } else if (req.get("format").is_obj()) {
            std::string key = req.get("format").dump();
            auto it = gram_.find(key);
            if (it == gram_.end()) it = gram_.emplace(key, tbtai::json_schema_gbnf(req.get("format"))).first;
            if (!it->second.empty()) {
                llama_sampler *g = llama_sampler_init_grammar(vocab_, it->second.c_str(), "root");
                if (!g) { llama_sampler_free(sm); err = "约束语法无效"; return false; }
                llama_sampler_chain_add(sm, g);
            }
        }
        double temp = req.get("temperature").num(0);
        if (temp <= 0) llama_sampler_chain_add(sm, llama_sampler_init_greedy());
        else {
            llama_sampler_chain_add(sm, llama_sampler_init_temp(float(temp)));
            llama_sampler_chain_add(sm, llama_sampler_init_dist(1234));
        }
        int i = 0;
        bool ok = true;
        for (; i < max_t; i++) {
            if (!tok("")) { err = "cancelled"; ok = false; break; }
            llama_token t = llama_sampler_sample(sm, ctx_, -1);
            if (llama_vocab_is_eog(vocab_, t)) break;
            char pb[256];
            int pn = llama_token_to_piece(vocab_, t, pb, int32_t(sizeof pb), 0, false);
            std::string piece(pb, size_t(std::max(pn, 0)));
            text += piece;
            if (!tok(piece)) { err = "cancelled"; ok = false; break; }
            if (llama_decode(ctx_, llama_batch_get_one(&t, 1)) != 0) { err = "推理失败"; ok = false; break; }
            cached_.push_back(t);
        }
        llama_sampler_free(sm);
        stats["tokens"] = i;
        return ok;
    }

private:
    llama_model *mdl_ = nullptr;
    llama_context *ctx_ = nullptr;
    const llama_vocab *vocab_ = nullptr;
    std::string tmpl_;
    bool think_ = false;
    std::vector<llama_token> cached_;              // 现在 KV 缓存里的词元（序列 0）
    std::map<std::string, std::string> gram_;      // Schema → GBNF
};
#endif

struct Pending { int id; Json req; };

}  // namespace

int main(int argc, char **argv) {
    std::map<std::string, std::string> a;
    for (int i = 1; i < argc; i++) {
        std::string s = argv[i];
        if (s.rfind("--", 0) != 0) continue;
        size_t e = s.find('=');
        if (e == std::string::npos) a[s.substr(2)] = "1";
        else a[s.substr(2, e - 2)] = s.substr(e + 1);
    }
#ifndef _WIN32
    setpriority(PRIO_PROCESS, 0, 10);   // 比游戏低一档：推理和模拟抢 CPU 时让着画面
#endif
    std::string bname = a.count("backend") ? a["backend"] : "mock";
    std::unique_ptr<Backend> be;
    std::string err;
    if (bname == "mock") {
        auto m = std::make_unique<MockBackend>();
        if (a.count("mock-delay")) m->delay_ms = std::atoi(a["mock-delay"].c_str());
        if (a.count("mock-crash-after")) m->crash_after = std::atoi(a["mock-crash-after"].c_str());
        m->hang = a.count("mock-hang") > 0;
        be = std::move(m);
    }
#ifdef TBT_WITH_LLAMA
    else if (bname == "llama") {
        auto l = std::make_unique<LlamaBackend>();
        if (a.count("model")) l->path = a["model"];
        if (a.count("lora")) l->lora = a["lora"];
        if (a.count("threads")) l->threads = std::atoi(a["threads"].c_str());
        if (a.count("ctx")) l->n_ctx = std::atoi(a["ctx"].c_str());
        be = std::move(l);
    }
#endif
    Json hello = Json::object();
    hello["type"] = "hello";
    hello["proto"] = "tbt-ai";
    hello["version"] = 1;
    hello["backend"] = bname;
    if (!be) {
        hello["ok"] = false;
        hello["error"] = bname == "llama" ? "这个版本没有编进 llama.cpp（cmake -DTBT_AI_LLAMA=ON）" : "未知后端 " + bname;
        emit(hello);
        return 2;
    }
    hello["model"] = be->model();
    if (!be->init(err)) {
        hello["ok"] = false;
        hello["error"] = err;
        emit(hello);
        return 2;
    }
    hello["ok"] = true;
    emit(hello);

#ifndef _WIN32
    const pid_t parent = getppid();
#endif
    std::mutex m;
    std::condition_variable cv;
    std::deque<Pending> q;
    std::atomic<int> current{-1};
    std::atomic<int> cancel_id{-1};
    std::atomic<bool> quit{false};

    std::thread reader([&] {
        std::string line;
        while (std::getline(std::cin, line)) {
            Json j;
            if (!Json::parse(line, j) || !j.is_obj()) continue;
            const std::string &type = j.get("type").str();
            int id = int(j.get("id").num(-1));
            if (type == "ping") {
                Json p = Json::object();
                p["type"] = "pong";
                p["id"] = id;
                emit(p);
            } else if (type == "cancel") {
                std::lock_guard<std::mutex> lk(m);
                for (auto it = q.begin(); it != q.end(); ++it)
                    if (it->id == id) { q.erase(it); break; }
                if (current == id) cancel_id = id;
            } else if (type == "generate") {
                std::lock_guard<std::mutex> lk(m);   // 入队和 quit 一样持锁
                if (q.size() >= 8) {
                    Json e = Json::object();
                    e["type"] = "error"; e["id"] = id; e["error"] = "busy";
                    emit(e);
                } else q.push_back({id, j});
                cv.notify_one();
            } else if (type == "shutdown") break;
        }
        {
            std::lock_guard<std::mutex> lk(m);   // 持锁改：否则主线程可能刚检查完条件、还没睡下就错过这次唤醒
            quit = true;   // stdin 关了（游戏退出）或要求关闭
        }
        cv.notify_one();
    });

    while (true) {
        Pending p;
        {
            std::unique_lock<std::mutex> lk(m);
            // 每秒看一眼父进程还在不在（游戏被强杀时双保险：socket 关闭本来就会让 stdin 读到结尾）
            while (!cv.wait_for(lk, std::chrono::seconds(1), [&] { return quit || !q.empty(); })) {
#ifndef _WIN32
                if (getppid() != parent) { quit = true; break; }
#endif
            }
            if (quit) break;
            p = std::move(q.front());
            q.pop_front();
            current = p.id;
        }
        double t0 = now_ms();
        std::string text, gerr;
        Json stats = Json::object();
        int id = p.id;
        bool ok = be->generate(p.req, [&](const std::string &piece) {
            if (cancel_id == id || quit) return false;
            if (!piece.empty()) {
                Json t = Json::object();
                t["type"] = "token"; t["id"] = id; t["text"] = piece;
                emit(t);
            }
            return true;
        }, text, gerr, stats);
        current = -1;
        if (cancel_id == id) { cancel_id = -1; continue; }   // 取消的请求不回结果（游戏那边已经结束它了）
        Json r = Json::object();
        r["id"] = id;
        if (ok) {
            r["type"] = "done";
            r["text"] = text;
            r["ms"] = now_ms() - t0;
            r["tokens"] = stats.get("tokens");
            r["prompt_tokens"] = stats.get("prompt_tokens");
        } else {
            r["type"] = "error";
            r["error"] = gerr;
        }
        emit(r);
    }
    std::fflush(stdout);
    std::_Exit(0);   // reader 可能还卡在 getline 上：直接退出
}
