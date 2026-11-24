// Minimal JSON writer (no parsing) used for the state/candidate exports.
#pragma once

#include <cmath>
#include <cstdio>
#include <string>
#include <vector>

namespace tbt {

class Json {
 public:
  Json& obj() { sep(); s_ += '{'; first_.push_back(true); return *this; }
  Json& end_obj() { s_ += '}'; first_.pop_back(); return *this; }
  Json& arr() { sep(); s_ += '['; first_.push_back(true); return *this; }
  Json& end_arr() { s_ += ']'; first_.pop_back(); return *this; }
  Json& key(const char* k) { sep(); quote(k); s_ += ':'; after_key_ = true; return *this; }
  Json& i(long long v) { sep(); s_ += std::to_string(v); return *this; }
  Json& b(bool v) { sep(); s_ += v ? "true" : "false"; return *this; }
  Json& null() { sep(); s_ += "null"; return *this; }
  Json& d(double v) {
    sep();
    if (!std::isfinite(v)) { s_ += "null"; return *this; }
    char buf[32];
    std::snprintf(buf, sizeof(buf), "%.6g", v);
    s_ += buf;
    return *this;
  }
  Json& str(const std::string& v) { sep(); quote(v); return *this; }
  const std::string& out() const { return s_; }

 private:
  void sep() {
    if (after_key_) { after_key_ = false; return; }
    if (!first_.empty()) {
      if (!first_.back()) s_ += ',';
      first_.back() = false;
    }
  }
  void quote(const std::string& v) {
    s_ += '"';
    for (char c : v) {
      switch (c) {
        case '"': s_ += "\\\""; break;
        case '\\': s_ += "\\\\"; break;
        case '\n': s_ += "\\n"; break;
        default: s_ += c;
      }
    }
    s_ += '"';
  }
  std::string s_;
  std::vector<bool> first_;
  bool after_key_ = false;
};

}  // namespace tbt
