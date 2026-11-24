#include "json.hpp"

#include <cmath>
#include <cstdio>
#include <cstdlib>

namespace tbtai {

namespace {
const Json kNull;
const std::string kEmpty;

struct Parser {
    const std::string &s;
    size_t i = 0;
    std::string err;
    int depth = 0;

    bool fail(const char *why) {
        if (err.empty()) err = std::string(why) + "（第 " + std::to_string(i) + " 字节）";
        return false;
    }
    void ws() { while (i < s.size() && (s[i] == ' ' || s[i] == '\t' || s[i] == '\n' || s[i] == '\r')) i++; }
    bool lit(const char *w) {
        size_t n = std::char_traits<char>::length(w);
        if (s.compare(i, n, w) != 0) return fail("无法识别的值");
        i += n;
        return true;
    }
    static void utf8(std::string &o, unsigned cp) {
        if (cp < 0x80) o += char(cp);
        else if (cp < 0x800) { o += char(0xC0 | (cp >> 6)); o += char(0x80 | (cp & 0x3F)); }
        else if (cp < 0x10000) { o += char(0xE0 | (cp >> 12)); o += char(0x80 | ((cp >> 6) & 0x3F)); o += char(0x80 | (cp & 0x3F)); }
        else { o += char(0xF0 | (cp >> 18)); o += char(0x80 | ((cp >> 12) & 0x3F)); o += char(0x80 | ((cp >> 6) & 0x3F)); o += char(0x80 | (cp & 0x3F)); }
    }
    bool hex4(unsigned &v) {
        if (i + 4 > s.size()) return fail("\\u 转义不完整");
        v = 0;
        for (int k = 0; k < 4; k++) {
            char c = s[i++];
            v <<= 4;
            if (c >= '0' && c <= '9') v |= unsigned(c - '0');
            else if (c >= 'a' && c <= 'f') v |= unsigned(c - 'a' + 10);
            else if (c >= 'A' && c <= 'F') v |= unsigned(c - 'A' + 10);
            else return fail("\\u 转义里有非十六进制字符");
        }
        return true;
    }
    bool string(std::string &o) {
        i++;   // "
        while (true) {
            if (i >= s.size()) return fail("字符串没有结束");
            char c = s[i++];
            if (c == '"') return true;
            if (unsigned(c) < 0x20) return fail("字符串里有控制字符");
            if (c != '\\') { o += c; continue; }
            if (i >= s.size()) return fail("转义不完整");
            char e = s[i++];
            switch (e) {
                case '"': o += '"'; break;
                case '\\': o += '\\'; break;
                case '/': o += '/'; break;
                case 'b': o += '\b'; break;
                case 'f': o += '\f'; break;
                case 'n': o += '\n'; break;
                case 'r': o += '\r'; break;
                case 't': o += '\t'; break;
                case 'u': {
                    unsigned cp;
                    if (!hex4(cp)) return false;
                    if (cp >= 0xD800 && cp < 0xDC00) {   // 代理对
                        unsigned lo;
                        if (i + 2 > s.size() || s[i] != '\\' || s[i + 1] != 'u') return fail("代理对缺后半");
                        i += 2;
                        if (!hex4(lo)) return false;
                        if (lo < 0xDC00 || lo >= 0xE000) return fail("代理对后半无效");
                        cp = 0x10000 + ((cp - 0xD800) << 10) + (lo - 0xDC00);
                    } else if (cp >= 0xDC00 && cp < 0xE000) return fail("孤立的代理对后半");
                    utf8(o, cp);
                    break;
                }
                default: return fail("未知转义");
            }
        }
    }
    bool number(Json &out) {
        size_t b = i;
        if (s[i] == '-') i++;
        if (i >= s.size() || !(s[i] >= '0' && s[i] <= '9')) return fail("数字格式不对");
        while (i < s.size() && ((s[i] >= '0' && s[i] <= '9') || s[i] == '.' || s[i] == 'e' || s[i] == 'E' || s[i] == '+' || s[i] == '-')) i++;
        std::string t = s.substr(b, i - b);
        char *end = nullptr;
        double v = std::strtod(t.c_str(), &end);
        if (!end || *end) return fail("数字格式不对");
        out = Json(v);
        return true;
    }
    bool value(Json &out) {
        ws();
        if (i >= s.size()) return fail("缺少值");
        if (++depth > 64) return fail("嵌套太深");
        bool ok = true;
        char c = s[i];
        if (c == '{') {
            out = Json::object();
            i++;
            ws();
            if (i < s.size() && s[i] == '}') { i++; }
            else while (true) {
                ws();
                if (i >= s.size() || s[i] != '"') { ok = fail("对象的键必须是字符串"); break; }
                std::string k;
                if (!string(k)) { ok = false; break; }
                ws();
                if (i >= s.size() || s[i] != ':') { ok = fail("缺少冒号"); break; }
                i++;
                Json v;
                if (!value(v)) { ok = false; break; }
                out[k] = std::move(v);
                ws();
                if (i < s.size() && s[i] == ',') { i++; continue; }
                if (i < s.size() && s[i] == '}') { i++; break; }
                ok = fail("对象缺少逗号或右括号");
                break;
            }
        } else if (c == '[') {
            out = Json::array();
            i++;
            ws();
            if (i < s.size() && s[i] == ']') { i++; }
            else while (true) {
                Json v;
                if (!value(v)) { ok = false; break; }
                out.push(std::move(v));
                ws();
                if (i < s.size() && s[i] == ',') { i++; continue; }
                if (i < s.size() && s[i] == ']') { i++; break; }
                ok = fail("数组缺少逗号或右括号");
                break;
            }
        } else if (c == '"') {
            std::string v;
            ok = string(v);
            if (ok) out = Json(std::move(v));
        } else if (c == 't') { ok = lit("true"); out = Json(true); }
        else if (c == 'f') { ok = lit("false"); out = Json(false); }
        else if (c == 'n') { ok = lit("null"); out = Json(); }
        else ok = number(out);
        depth--;
        return ok;
    }
};
}  // namespace

const std::string &Json::str() const { return t_ == T::Str ? s_ : kEmpty; }

Json &Json::operator[](const std::string &k) {
    if (t_ != T::Obj) { *this = object(); }
    for (size_t i = 0; i < k_.size(); i++) if (k_[i] == k) return a_[i];
    k_.push_back(k);
    a_.emplace_back();
    return a_.back();
}

const Json &Json::get(const std::string &k) const {
    if (t_ == T::Obj) for (size_t i = 0; i < k_.size(); i++) if (k_[i] == k) return a_[i];
    return kNull;
}

bool Json::has(const std::string &k) const {
    if (t_ == T::Obj) for (const auto &x : k_) if (x == k) return true;
    return false;
}

void Json::erase(const std::string &k) {
    if (t_ != T::Obj) return;
    for (size_t i = 0; i < k_.size(); i++)
        if (k_[i] == k) { k_.erase(k_.begin() + long(i)); a_.erase(a_.begin() + long(i)); return; }
}

void Json::push(Json v) {
    if (t_ != T::Arr) *this = array();
    a_.push_back(std::move(v));
}

std::string json_quote(const std::string &s) {
    std::string o = "\"";
    for (unsigned char c : s) {
        switch (c) {
            case '"': o += "\\\""; break;
            case '\\': o += "\\\\"; break;
            case '\n': o += "\\n"; break;
            case '\r': o += "\\r"; break;
            case '\t': o += "\\t"; break;
            default:
                if (c < 0x20) { char b[8]; std::snprintf(b, sizeof b, "\\u%04x", c); o += b; }
                else o += char(c);
        }
    }
    return o + "\"";
}

void Json::dump_to(std::string &out) const {
    switch (t_) {
        case T::Null: out += "null"; break;
        case T::Bool: out += n_ != 0 ? "true" : "false"; break;
        case T::Num: {
            if (!std::isfinite(n_)) { out += "null"; break; }
            char b[32];
            if (n_ == std::floor(n_) && std::fabs(n_) < 1e15) std::snprintf(b, sizeof b, "%.0f", n_);
            else std::snprintf(b, sizeof b, "%.10g", n_);
            out += b;
            break;
        }
        case T::Str: out += json_quote(s_); break;
        case T::Arr:
            out += '[';
            for (size_t i = 0; i < a_.size(); i++) { if (i) out += ','; a_[i].dump_to(out); }
            out += ']';
            break;
        case T::Obj:
            out += '{';
            for (size_t i = 0; i < k_.size(); i++) {
                if (i) out += ',';
                out += json_quote(k_[i]);
                out += ':';
                a_[i].dump_to(out);
            }
            out += '}';
            break;
    }
}

std::string Json::dump() const {
    std::string o;
    dump_to(o);
    return o;
}

bool Json::parse(const std::string &src, Json &out, std::string *err) {
    Parser p{src, 0, {}, 0};
    Json v;
    bool ok = p.value(v);
    if (ok) {
        p.ws();
        if (p.i != src.size()) ok = p.fail("值后面还有多余内容");
    }
    if (!ok) { if (err) *err = p.err; return false; }
    out = std::move(v);
    return true;
}

bool Json::operator==(const Json &o) const {
    if (t_ != o.t_) return false;
    switch (t_) {
        case T::Null: return true;
        case T::Bool: case T::Num: return n_ == o.n_;
        case T::Str: return s_ == o.s_;
        case T::Arr: return a_ == o.a_;
        case T::Obj: return k_ == o.k_ && a_ == o.a_;
    }
    return false;
}

namespace {
std::string gbnf_lit(const std::string &raw) {   // GBNF 字面量：双引号里转义引号和反斜杠
    std::string o = "\"";
    for (char c : raw) {
        if (c == '"' || c == '\\') o += '\\';
        o += c;
    }
    return o + "\"";
}

struct Gbnf {
    std::vector<std::string> rules;
    int n = 0;
    bool ok = true;
    std::string value(const Json &s) {
        if (s.get("enum").is_arr()) {
            std::string alt;
            for (const auto &e : s.get("enum").elems()) {
                if (!alt.empty()) alt += " | ";
                alt += gbnf_lit(e.dump());
            }
            if (alt.empty()) ok = false;
            return "(" + alt + ")";
        }
        const std::string &t = s.get("type").str();
        if (t == "string") return "str";
        if (t == "number") return "num";
        if (t == "integer") return "int";
        if (t == "boolean") return "(\"true\" | \"false\")";
        if (t == "object" || s.get("properties").is_obj()) return object(s);
        ok = false;
        return "";
    }
    std::string object(const Json &s) {
        const Json &props = s.get("properties");
        std::vector<std::string> req, opt;
        for (const auto &k : props.keys()) {
            bool r = false;
            for (const auto &q : s.get("required").elems()) if (q.str() == k) r = true;
            (r ? req : opt).push_back(k);
        }
        if (req.empty()) { ok = false; return ""; }
        std::string name = "obj" + std::to_string(n++);
        auto member = [&](const std::string &k) { return gbnf_lit(json_quote(k)) + " ws \":\" ws " + value(props.get(k)); };
        std::string body = "\"{\" ws ";
        for (size_t i = 0; i < req.size(); i++) body += (i ? " \",\" ws " : "") + member(req[i]);
        for (const auto &k : opt) body += " (\",\" ws " + member(k) + ")?";
        body += " ws \"}\"";
        rules.push_back(name + " ::= " + body);
        return name;
    }
};
}  // namespace

std::string json_schema_gbnf(const Json &schema) {
    Gbnf g;
    std::string top = g.value(schema);
    if (!g.ok || top.empty()) return "";
    std::string out = "root ::= " + top + "\n";
    for (const auto &r : g.rules) out += r + "\n";
    out += "ws ::= ([ \\t\\n] ([ \\t\\n] ([ \\t\\n] [ \\t\\n]?)?)?)?\n";
    out += "str ::= \"\\\"\" ([^\"\\\\\\x00-\\x1f] | \"\\\\\" [\"\\\\/bfnrt]){0,64} \"\\\"\"\n";
    out += "int ::= \"-\"? ([0-9] | [1-9] [0-9]{1,15})\n";
    out += "num ::= int (\".\" [0-9]{1,8})?\n";
    return out;
}

}  // namespace tbtai
