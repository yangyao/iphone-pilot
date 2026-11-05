#include <cassert>
#include "../../hid_bridge/absolute_pointer.h"
int main() {
  long p[5];
  assert(absoluteArgs("0 32767",p,2) && p[0]==0 && p[1]==32767);
  for(const char *bad:{"-1 0","32768 0","1","1 2 3","1x 2","99999999999999999 0"})
    assert(!absoluteArgs(bad,p,2));
  assert(absoluteArgs("1 2 3 4 400",p,5));
  AbsolutePointer a;
  uint8_t d[64];
  assert(a._onGetDescriptor(d)==USBHID::registered && USBHID::registered==54);
  assert(a.move(32767,0));
  assert(USBHID::reports.size()==8);
  auto r=USBHID::reports.back();
  assert(r==std::vector<uint8_t>({7,0,255,127,0,0}));
  assert(a.send(0,32767,1));
  assert(USBHID::reports.back()==std::vector<uint8_t>({7,1,0,0,255,127}));
}
