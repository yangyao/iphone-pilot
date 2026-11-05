// Local hybrid extension: independent absolute report alongside upstream relative HID.
#pragma once
#include "USBHID.h"

class AbsolutePointer : public USBHIDDevice {
  USBHID hid;
  uint16_t x_=16384, y_=16384;
 public:
  AbsolutePointer() { hid.addDevice(this, 54); }
  uint16_t _onGetDescriptor(uint8_t *dst) override {
    static const uint8_t desc[]={
      0x05,0x01,0x09,0x02,0xA1,0x01,0x85,0x07,0x09,0x01,0xA1,0x00,
      0x05,0x09,0x19,0x01,0x29,0x03,0x15,0x00,0x25,0x01,
      0x95,0x03,0x75,0x01,0x81,0x02,0x95,0x01,0x75,0x05,0x81,0x01,
      0x05,0x01,0x09,0x30,0x09,0x31,0x16,0x00,0x00,0x26,0xFF,0x7F,
      0x75,0x10,0x95,0x02,0x81,0x02,0xC0,0xC0};
    static_assert(sizeof(desc)==54,"descriptor registration length");
    memcpy(dst,desc,sizeof(desc)); return sizeof(desc);
  }
  void begin() { hid.begin(); }
  bool send(uint16_t x,uint16_t y,uint8_t buttons=0) {
    uint8_t r[]={buttons,uint8_t(x),uint8_t(x>>8),uint8_t(y),uint8_t(y>>8)};
    bool ok=hid.SendReport(7,r,sizeof(r));
    if(ok) { x_=x; y_=y; } return ok;
  }
  bool move(uint16_t x,uint16_t y) {
    int32_t sx=x_,sy=y_;
    for(int i=1;i<=8;i++) {
      if(!send(sx+(int32_t(x)-sx)*i/8,sy+(int32_t(y)-sy)*i/8)) return false;
      delay(8);
    }
    return true;
  }
};

// Parse exactly N unsigned decimal values; reject missing, trailing and out-of-range data.
static bool absoluteArgs(const String &text,long *out,int count) {
  const char *p=text.c_str();
  for(int i=0;i<count;i++) {
    while(*p==' ') p++;
    if(*p<'0'||*p>'9') return false;
    long n=0;
    while(*p>='0'&&*p<='9') { n=n*10+(*p++-'0'); if(n>32767) return false; }
    if(*p && *p!=' ') return false;
    out[i]=n;
  }
  while(*p==' ') p++;
  return !*p;
}
