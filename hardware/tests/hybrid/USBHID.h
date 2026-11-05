#pragma once
#include <cstdint>
#include <cstring>
#include <string>
#include <vector>
using String=std::string;
inline void delay(int) {}
class USBHIDDevice { public: virtual uint16_t _onGetDescriptor(uint8_t*) { return 0; } };
class USBHID {
 public:
  static inline int registered=0;
  static inline std::vector<std::vector<uint8_t>> reports;
  static bool addDevice(USBHIDDevice*,int n) { registered=n; return true; }
  void begin() {}
  bool SendReport(uint8_t id,const void *data,size_t n) {
    auto p=static_cast<const uint8_t*>(data);
    reports.push_back({id}); reports.back().insert(reports.back().end(),p,p+n); return true;
  }
};
