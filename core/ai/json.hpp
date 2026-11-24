#pragma once
// 小型 JSON 值（复制自 FromLev core/include/stardrift/json.hpp；本地 AI 推理进程的协议用）：解析 + 输出。
// 对象保持插入顺序，输出可复现（测试和日志能直接比文本）。数字一律 double；NaN / ∞ 输出为 null。
#include <cstddef>
#include <string>
#include <vector>

namespace tbtai {

class Json {
public:
    enum class T { Null, Bool, Num, Str, Arr, Obj };

    Json() = default;
    Json(std::nullptr_t) {}
    Json(bool v) : t_(T::Bool), n_(v ? 1 : 0) {}
    Json(double v) : t_(T::Num), n_(v) {}
    Json(int v) : t_(T::Num), n_(v) {}
    Json(long long v) : t_(T::Num), n_(double(v)) {}
    Json(const char *v) : t_(T::Str), s_(v) {}
    Json(std::string v) : t_(T::Str), s_(std::move(v)) {}
    static Json array() { Json j; j.t_ = T::Arr; return j; }
    static Json object() { Json j; j.t_ = T::Obj; return j; }

    T type() const { return t_; }
    bool is_null() const { return t_ == T::Null; }
    bool is_bool() const { return t_ == T::Bool; }
    bool is_num() const { return t_ == T::Num; }
    bool is_str() const { return t_ == T::Str; }
    bool is_arr() const { return t_ == T::Arr; }
    bool is_obj() const { return t_ == T::Obj; }

    double num(double def = 0) const { return t_ == T::Num ? n_ : def; }
    bool boolean(bool def = false) const { return t_ == T::Bool ? n_ != 0 : def; }
    const std::string &str() const;           // 不是字符串时返回空串
    std::string str_or(const std::string &def) const { return t_ == T::Str ? s_ : def; }

    // 对象：operator[] 不存在就新建（Null 会先变成对象）；get 不存在返回 null
    Json &operator[](const std::string &k);
    const Json &get(const std::string &k) const;
    bool has(const std::string &k) const;
    void erase(const std::string &k);
    const std::vector<std::string> &keys() const { return k_; }

    // 数组（Null 会先变成数组）；对象的值也按 keys() 的顺序放在这里
    void push(Json v);
    size_t size() const { return a_.size(); }
    const Json &at(size_t i) const { return a_[i]; }
    const std::vector<Json> &elems() const { return a_; }

    std::string dump() const;
    /// 解析整段文本（前后允许空白）；失败返回 false，err 写位置与原因
    static bool parse(const std::string &src, Json &out, std::string *err = nullptr);

    bool operator==(const Json &o) const;

private:
    T t_ = T::Null;
    double n_ = 0;
    std::string s_;
    std::vector<std::string> k_;   // 对象的键
    std::vector<Json> a_;          // 数组元素 / 对象的值
    void dump_to(std::string &out) const;
};

/// 把文本按 JSON 字符串规则转义（带引号）
std::string json_quote(const std::string &s);

/// JSON Schema（本地 AI 用到的子集）→ llama.cpp 的 GBNF 语法，用于约束解码：
///   object（properties + required；必填的键先、可选的在后，按声明顺序）、enum（字符串 / 数字）、
///   type = string（最长 64 字）/ number / integer / boolean，对象可以嵌套。
/// 不支持的写法返回空串（调用方就不加约束）
std::string json_schema_gbnf(const Json &schema);

}  // namespace tbtai
