// SPDX-License-Identifier: MIT
// Framebuffer/font primitives adapted from Omarchy psX (see provenance.json).
#define _POSIX_C_SOURCE 200809L
#include <proto-include.h>
#include <orbis/Pad.h>
#include <orbis/Sysmodule.h>
#include <orbis/UserService.h>
#include <orbis/VideoOut.h>
#include <orbis/libkernel.h>
#include <errno.h>
#include <fcntl.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <unistd.h>
#include "bundle.hpp"
#include "bundle_pins.hpp"
#include "console_read.hpp"
#include "controller.hpp"
#include "ui.hpp"
extern "C" int sysctlbyname(const char*, void*, size_t*, const void*, size_t);
namespace {
namespace ui = diagnostix::ui;
using diagnostix::Result;
constexpr int kWidth=ui::kWidth, kHeight=ui::kHeight, kDepth=4;
constexpr size_t kDirectAlignment=0x200000;
class Framebuffer {
 public:
  bool init() {
    video_ = sceVideoOutOpen(ORBIS_VIDEO_USER_MAIN, ORBIS_VIDEO_OUT_BUS_MAIN, 0, 0);
    if (video_ < 0) return false;

    if (sceKernelCreateEqueue(&flip_queue_, "diagnostix-ui-flip") < 0) return false;
    if (sceVideoOutAddFlipEvent(flip_queue_, video_, nullptr) < 0) return false;

    const size_t frame_size = static_cast<size_t>(kWidth) * kHeight * kDepth;
    const size_t requested = frame_size * 2;
    direct_size_ = (requested + kDirectAlignment - 1) / kDirectAlignment * kDirectAlignment;

    if (sceKernelAllocateDirectMemory(0, sceKernelGetDirectMemorySize(), direct_size_,
                                      kDirectAlignment, 3, &direct_offset_) < 0) {
      return false;
    }
    if (sceKernelMapDirectMemory(&direct_memory_, direct_size_, 0x33, 0,
                                 direct_offset_, kDirectAlignment) < 0) {
      return false;
    }

    buffers_[0] = static_cast<uint32_t*>(direct_memory_);
    buffers_[1] = reinterpret_cast<uint32_t*>(
        reinterpret_cast<uint8_t*>(direct_memory_) + frame_size);

    OrbisVideoOutBufferAttribute attribute;
    sceVideoOutSetBufferAttribute(&attribute, 0x80000000, 1, 0,
                                  kWidth, kHeight, kWidth);
    if (sceVideoOutRegisterBuffers(video_, 0,
                                   reinterpret_cast<void**>(buffers_), 2,
                                   &attribute) < 0) {
      return false;
    }
    sceVideoOutSetFlipRate(video_, 0);
    return true;
  }

  ui::Surface& canvas() { surface_.target(buffers_[active_]); return surface_; }

  void present(int frame_id) {
    if (sceVideoOutSubmitFlip(video_, active_, ORBIS_VIDEO_OUT_FLIP_VSYNC, frame_id) < 0)
      display_failure();
    for (int attempt = 0; attempt < 8; ++attempt) {
      OrbisVideoOutFlipStatus status{};
      if (sceVideoOutGetFlipStatus(video_, &status) < 0) display_failure();
      if (status.flipArg == frame_id) { active_ = (active_ + 1) % 2; return; }
      OrbisKernelEvent event{};
      int count = 0;
      OrbisKernelUseconds timeout = 250000;
      if (sceKernelWaitEqueue(flip_queue_, &event, 1, &count, &timeout) != 0)
        display_failure();
    }
    display_failure();
  }

 private:
  [[noreturn]] static void display_failure() {
    // Termination preserves any durable install journal for explicit recovery.
    fprintf(stderr, "[diagnostix] display flip failed; app stopped\n");
    exit(5);
  }

  ui::Surface surface_;
  int video_ = -1;
  int active_ = 0;
  off_t direct_offset_ = 0;
  size_t direct_size_ = 0;
  void* direct_memory_ = nullptr;
  uint32_t* buffers_[2] = {nullptr, nullptr};
  OrbisKernelEqueue flip_queue_ = 0;
};
struct Fonts {
  FT_Library library = nullptr;
  ui::Faces faces;

  bool init() {
    if (sceSysmoduleLoadModule(ORBIS_SYSMODULE_FREETYPE_OL) < 0) return false;
    if (FT_Init_FreeType(&library) != 0) return false;
    for (const ui::FaceSpec& spec : ui::kFaceSpecs) {
      if (!load(&(faces.*spec.slot), spec.pixels, spec.bold)) return false;
    }
    return true;
  }

 private:
  bool load(FT_Face* face, int size, bool bold) {
    const char* system_font = bold
        ? "/preinst/common/font/SST-Bold.otf"
        : "/preinst/common/font/SST-Roman.otf";
    if (FT_New_Face(library, system_font, 0, face) != 0) {
      if (FT_New_Face(library, "/app0/assets/fonts/Gontserrat-Regular.ttf", 0,
                      face) != 0) {
        return false;
      }
    }
    return FT_Set_Pixel_Sizes(*face, 0, size) == 0;
  }
};

Framebuffer screen;
Fonts fonts;
int frame_id=0;
void progress(const diagnostix::ProgressUpdate& update,void*) {
  // Redraw only every 8 MiB, plus the start/end of each file.
  if (!update.complete && update.file_done != update.file_total && update.file_done > 65536 && update.file_done % 8388608) return;
  ui::draw_progress(screen.canvas(),fonts.faces,update);
  screen.present(++frame_id);
}
void launch_boot(diagnostix::Bundle& bundle,diagnostix::Result& result,bool& ready,bool& attempted,std::string& status,bool manual) {
  if(!ready || attempted)return;
  attempted=true;ready=false;
  result=bundle.boot(manual);status=bundle.status(result);
}
void launch_install(diagnostix::Bundle& bundle,diagnostix::Result& result,bool& ready,bool attempted,std::string& status) {
  if(attempted){result=diagnostix::Result::AlreadyAttempted;ready=false;status=bundle.status(result);return;}
  result=bundle.install();ready=result==diagnostix::Result::Ready&&!attempted;status=bundle.status(result);
}
// Every line reaches UART, so a launcher problem can be read without the screen.
void trace(const char* format,...) {
  char line[256]; va_list arguments; va_start(arguments,format);
  vsnprintf(line,sizeof(line),format,arguments); va_end(arguments);
  fprintf(stderr,"[diagnostix] %s\n",line);
}
// Asks how big the value is first, because a too-small buffer is a failure of its own.
void probe_sysctl(const char* name) {
  size_t size=0; errno=0;
  const int asked=sysctlbyname(name,nullptr,&size,nullptr,0); const int ask_error=errno;
  uint64_t value=0; size_t length=sizeof(value); errno=0;
  const int fetched=sysctlbyname(name,&value,&length,nullptr,0);
  trace("sysctl %s: size rc=%d errno=%d bytes=%zu; read rc=%d errno=%d bytes=%zu value=0x%llx",name,asked,ask_error,size,fetched,errno,length,(unsigned long long)value);
}
int read_board(uint32_t* value,size_t* length){return sysctlbyname("hw.sce_subsys_subid",value,length,nullptr,0);}
// GoldHEN's SDK entry: syscall 500 with a command and a buffer. Its answer can come back
// flagged as a kernel error, so the raw register is the answer either way.
long goldhen_sdk(uint64_t command,void* data) {
  long result;
  asm volatile("syscall":"=a"(result):"0"(500L),"D"(command),"S"(data)
               :"rcx","rdx","r8","r9","r10","r11","memory","cc");
  return result;
}
void note_board(const char* step,long result,int error,uint32_t value){trace("southbridge %s: rc=%ld errno=%d value=0x%x",step,result,error,value);}
ui::Console read_console() {
  ui::Console console; uint32_t southbridge=0;
  console.southbridge_known=diagnostix::read_southbridge(diagnostix::BoardCalls{read_board,goldhen_sdk,note_board},&southbridge);
  console.southbridge=southbridge;
  OrbisKernelSwVersion version{}; version.Size=sizeof(version); errno=0;
  const int firmware=sceKernelGetSystemSwVersion(&version);
  trace("firmware: rc=0x%x errno=%d value=0x%08x text=%.27s",(unsigned)firmware,errno,version.Version,version.VersionString);
  console.firmware_known=firmware==0&&version.Version!=0; console.firmware=version.Version;
  console.pro=sceKernelIsNeoMode()!=0;
  trace("console: pro=%d southbridge_known=%d firmware_known=%d",console.pro,console.southbridge_known,console.firmware_known);
  // Other names that may answer here, so one boot shows every way to read these.
  for(const char* name:{"hw.sce_subsys_subid","kern.sdk_version","machdep.upd_version","kern.upd_version"})probe_sysctl(name);
  return console;
}
const char* usb_root() {
  for(const char* path:{"/mnt/usb0","/mnt/usb1"}) {
    const int directory=open(path,O_RDONLY|O_DIRECTORY);
    if(directory>=0){close(directory);return path;}
  }
  return nullptr;
}
bool save_console_info(const ui::Console& console) {
  const char* root=usb_root(); if(!root)return false;
  std::string path=std::string(root)+"/DiagnostiX";
  if(mkdir(path.c_str(),0777)!=0&&errno!=EEXIST)return false;
  path+="/console-info.txt";
  FILE* file=fopen(path.c_str(),"w"); if(!file)return false;
  const bool written=fprintf(file,"console: %s\nsouthbridge: %s (0x%06x)\nfirmware: %s\n",console.pro?"PS4 Pro":"PS4",console.southbridge_known?ui::southbridge_name(console.southbridge):"Unknown",console.southbridge&0xFFFFFF,console.firmware_known?ui::firmware_name(console.firmware).c_str():"Unknown")>0;
  return fclose(file)==0&&written;
}
// Written by Linux as tests run, so a run that never finished is visible here.
void refresh_last_run(ui::HomeView& home) {
  home.last=ui::LastRun::None; home.last_stage.clear();
  const char* root=usb_root(); if(!root)return;
  const std::string path=std::string(root)+"/DiagnostiX/last-run.txt";
  FILE* file=fopen(path.c_str(),"r"); if(!file)return;
  char buffer[512]; const size_t count=fread(buffer,1,sizeof(buffer),file); fclose(file);
  ui::read_last_run(std::string(buffer,count),&home);
}
ui::Files files_state(Result result) {
  switch(result) {
    case Result::Ready: return ui::Files::Ready;
    case Result::Missing: case Result::Recovered: return ui::Files::Missing;
    case Result::Interrupted: case Result::Invalid: return ui::Files::Repair;
    default: return ui::Files::Problem;
  }
}
enum class Page { Home, About, CleanupReview, CleanupConfirm, NoUsb, Starting };
} // namespace
int main() {
  setvbuf(stdout,nullptr,_IONBF,0); setvbuf(stderr,nullptr,_IONBF,0);
  trace("launcher starting");
  if(!screen.init()){trace("display setup failed");return 1;}
  if(!fonts.init()){trace("font setup failed");return 1;}
  trace("display and fonts ready");
  sceSysmoduleLoadModuleInternal(ORBIS_SYSMODULE_INTERNAL_NET);
  if(scePadInit()<0){trace("controller library failed");return 2;}
  OrbisUserServiceInitializeParams parameters{}; parameters.priority=ORBIS_KERNEL_PRIO_FIFO_LOWEST;
  sceUserServiceInitialize(&parameters); OrbisUserServiceUserId user;
  if(sceUserServiceGetInitialUser(&user)<0){trace("no signed-in user");return 3;}
  int pad=scePadOpen(user,0,0,nullptr); if(pad<0){trace("controller open failed: 0x%x",(unsigned)pad);return 4;}
  const size_t pin_count=sizeof(kBundlePins)/sizeof(kBundlePins[0]);
  diagnostix::Bundle bundle(kBundlePins,pin_count,progress,nullptr);
  // Startup hashes a matching installed candidate once; this launch reuses its snapshot.
  auto result=bundle.inspect(); bool ready=result==Result::Ready, handoff_attempted=false, manual=false;
  std::string status=bundle.status(result); diagnostix::CleanupPreview cleanup; size_t review_page=0;
  ui::HomeView home; home.console=read_console(); home.files=files_state(result);
  trace("home screen ready: files=%d ready=%d",(int)home.files,ready);
  for(size_t index=0;index<pin_count;++index)home.install_bytes+=kBundlePins[index].bytes;
  if(home.files==ui::Files::Problem)home.problem=status;
  Page page=Page::Home; int choice=0, frames=0; diagnostix::ControllerMapper controller;
  const ui::Faces& faces=fonts.faces;
  auto start=[&]{
    home.problem.clear(); home.notice.clear();
    if(!ready){
      launch_install(bundle,result,ready,handoff_attempted,status);
      if(result==Result::Recovered)launch_install(bundle,result,ready,handoff_attempted,status);
      home.files=files_state(result);
      if(!ready){home.problem=status;page=Page::Home;choice=0;return;}
    }
    ui::draw_starting(screen.canvas(),faces,manual); screen.present(++frame_id);
    trace("handing over to the loader");
    launch_boot(bundle,result,ready,handoff_attempted,status,manual);
    trace("handover returned: %s",status.c_str());
    if(result==Result::Sent){page=Page::Starting;}
    else{home.problem=status;home.files=files_state(result);page=Page::Home;choice=0;}
  };
  for(;;) {
    if(frames++%60==0){
      const bool usb=usb_root()!=nullptr;
      if(usb!=home.usb){home.usb=usb;refresh_last_run(home);trace("usb stick: %s",usb?"found":"gone");}
    }
    home.choice=choice;
    auto& canvas=screen.canvas();
    switch(page) {
      case Page::Home: ui::draw_home(canvas,faces,home); break;
      case Page::About: ui::draw_about(canvas,faces); break;
      case Page::CleanupReview: ui::draw_cleanup_review(canvas,faces,cleanup,review_page,choice); break;
      case Page::CleanupConfirm: ui::draw_cleanup_confirm(canvas,faces,cleanup,choice); break;
      case Page::NoUsb: ui::draw_no_usb(canvas,faces,choice); break;
      case Page::Starting: ui::draw_starting(canvas,faces,manual); break;
    }
    screen.present(++frame_id);
    OrbisPadData input{}; if(scePadReadState(pad,&input)<0){sceKernelUsleep(16000);continue;}
    const auto actions=controller.update(input.buttons,input.leftStick.y,input.connected);
    const std::vector<ui::Row> rows=ui::home_rows(home);
    const int count=page==Page::Home?static_cast<int>(rows.size()):2;
    if(page!=Page::About && page!=Page::Starting) {
      if(actions.move<0) choice=(choice+count-1)%count;
      if(actions.move>0) choice=(choice+1)%count;
    }
    if(actions.back && page!=Page::Starting){page=Page::Home;choice=0;}
    if(actions.confirm) {
      if(page==Page::Home && rows[choice].enabled) {
        home.problem.clear(); home.notice.clear();
        switch(rows[choice].action) {
          case ui::Action::Automatic: case ui::Action::Manual:
            manual=rows[choice].action==ui::Action::Manual;
            trace("chosen: %s test, usb=%d",manual?"manual":"automatic",home.usb);
            if(home.usb)start(); else{page=Page::NoUsb;choice=0;}
            break;
          case ui::Action::SaveInfo:
            if(save_console_info(home.console))home.notice="Saved to your USB stick as DiagnostiX/console-info.txt.";
            else home.problem="Could not write to the USB stick.";
            break;
          case ui::Action::Clear: {
            auto checked=handoff_attempted?diagnostix::CleanupResult::Blocked:bundle.preview_cleanup(&cleanup);
            if(checked==diagnostix::CleanupResult::Preview){page=Page::CleanupReview;choice=0;review_page=0;}
            else home.notice=diagnostix::message(checked);
            break;
          }
          case ui::Action::About: page=Page::About;choice=0; break;
        }
      } else if(page==Page::NoUsb) {
        if(choice==1)start(); else{page=Page::Home;choice=0;}
      } else if(page==Page::CleanupReview) {
        if(choice==0){page=Page::Home;choice=0;}
        else if((review_page+1)*3<cleanup.items.size()){++review_page;choice=0;}
        else {page=Page::CleanupConfirm;choice=0;}
      } else if(page==Page::CleanupConfirm) {
        if(choice==1) {
          uint64_t reclaimed=0; auto cleared=bundle.clear_old_files(&reclaimed);
          char line[192]; snprintf(line,sizeof(line),"%s Reclaimed %s.",diagnostix::message(cleared),ui::storage_size(reclaimed).c_str());home.notice=line;
        }
        page=Page::Home;choice=0;
      }
    }
    sceKernelUsleep(16000);
  }
}
