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
#include "native_stat.hpp"
#include "ui.hpp"
extern "C" int sysctlbyname(const char*, void*, size_t*, const void*, size_t);
extern "C" int sysctl(const int*, unsigned, void*, size_t*, const void*, size_t);
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
void launch_boot(diagnostix::Bundle& bundle,diagnostix::Result& result,bool& ready,bool& attempted,std::string& status,bool manual,const std::string& extra) {
  if(!ready || attempted)return;
  attempted=true;ready=false;
  result=bundle.boot(manual,extra);status=bundle.status(result);
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
void note_quiet(const char*,long,int,uint32_t){}
// The PS4 hides USB sticks from an installed app: no launcher log ever found one while
// GoldHEN's FTP saw the same stick mounted. Each use of the stick borrows root, as the
// southbridge read does, and gives it back before anything else runs. Nested uses share one.
int stick_depth=0; bool stick_root=false, stick_told=false; diagnostix::JailbreakBackup stick_backup{};
class StickAccess {
 public:
  StickAccess() {
    if(stick_depth++)return;
    stick_root=diagnostix::borrow_root(goldhen_sdk,note_quiet,&stick_backup);
    if(!stick_told){stick_told=true;trace("stick access: root %s",stick_root?"borrowed":"refused, using the app's own view");}
  }
  ~StickAccess() {
    if(--stick_depth||!stick_root)return;
    diagnostix::return_root(goldhen_sdk,note_quiet,&stick_backup); stick_root=false;
  }
  StickAccess(const StickAccess&)=delete; StickAccess& operator=(const StickAccess&)=delete;
};
// The empty mount folder exists without a stick, so a stick counts only when the
// folder is on another device than /mnt.
bool mounted(const char* path) {
  const int folder=open(path,O_RDONLY|O_DIRECTORY); if(folder<0)return false;
  const int parent=open("/mnt",O_RDONLY|O_DIRECTORY); if(parent<0){close(folder);return false;}
  struct stat inside{}, outside{};
  const bool found=diagnostix::native_fstat(folder,&inside)==0&&diagnostix::native_fstat(parent,&outside)==0&&inside.st_dev!=outside.st_dev;
  close(folder); close(parent);
  return found;
}
// Call with a StickAccess held.
const char* usb_root() {
  for(const char* path:{"/mnt/usb0","/mnt/usb1"}) if(mounted(path))return path;
  return nullptr;
}
// Empty when there is no stick or the folder can't be made.
std::string stick_folder() {
  const char* root=usb_root(); if(!root)return {};
  std::string path=std::string(root)+"/DiagnostiX";
  if(mkdir(path.c_str(),0777)!=0&&errno!=EEXIST)return {};
  return path;
}
// Flushed, because the file has to survive Linux taking over the console right after.
bool write_stick_file(const std::string& path,const std::string& text) {
  const int file=open(path.c_str(),O_WRONLY|O_CREAT|O_TRUNC,0666); if(file<0)return false;
  bool ok=write(file,text.data(),text.size())==static_cast<ssize_t>(text.size())&&fsync(file)==0;
  if(close(file)!=0)ok=false;
  return ok;
}
std::string sysctl_text(const char* name) {
  char value[128]={}; size_t length=sizeof(value)-1;
  return sysctlbyname(name,value,&length,nullptr,0)==0?std::string(value):std::string();
}
uint64_t sysctl_number(const char* name) {
  uint64_t value=0; size_t length=sizeof(value);
  return sysctlbyname(name,&value,&length,nullptr,0)==0?value:0;
}
// Only values that describe the model of hardware. Nothing that identifies one console.
bool save_console_info(const ui::Console& console) {
  StickAccess access;
  const std::string folder=stick_folder(); if(folder.empty())return false;
  char summary[256];
  snprintf(summary,sizeof(summary),"console: %s\nsouthbridge: %s (0x%06x)\nfirmware: %s (0x%08x)\n",console.pro?"PS4 Pro":"PS4",console.southbridge_known?ui::southbridge_name(console.southbridge):"Unknown",console.southbridge&0xFFFFFF,console.firmware_known?ui::firmware_name(console.firmware).c_str():"Unknown",console.firmware);
  std::string text=summary;
  const std::string cpu=sysctl_text("hw.model"); if(!cpu.empty())text+="cpu: "+cpu+"\n";
  if(const uint64_t cores=sysctl_number("hw.ncpu"))text+="cpu-cores: "+std::to_string(cores)+"\n";
  if(const uint64_t memory=sysctl_number("hw.physmem"))text+="memory-bytes: "+std::to_string(memory)+"\n";
  return write_stick_file(folder+"/console-info.txt",text);
}
// Linux replaces this once it reaches the stick. Until then it is the only sign that a
// start was tried, so a hang before that still shows on the next launch.
void mark_started() {
  StickAccess access;
  const std::string folder=stick_folder(); if(folder.empty()){trace("start marker: no stick");return;}
  trace("start marker: %s",write_stick_file(folder+"/last-run.txt","state=running\nstage=\nfolder=\n")?"saved":"failed");
}
void clear_started() {
  StickAccess access;
  const std::string folder=stick_folder(); if(!folder.empty())unlink((folder+"/last-run.txt").c_str());
}
// Names only, so no value is read: this shows what the PS4 system exposes.
void list_sysctl_names() {
  constexpr unsigned longest=22; int current[longest]={1}; size_t depth=1;
  for(int count=0;count<6000;++count) {
    int ask[longest+2]={0,2}; memcpy(ask+2,current,depth*sizeof(int));
    int next[longest]; size_t bytes=sizeof(next);
    if(sysctl(ask,depth+2,next,&bytes,nullptr,0)!=0){trace("sysctl list: ended after %d names, errno=%d",count,errno);return;}
    depth=bytes/sizeof(int); memcpy(current,next,bytes);
    ask[1]=1; memcpy(ask+2,current,depth*sizeof(int));
    char name[160]={}; size_t length=sizeof(name)-1;
    if(sysctl(ask,depth+2,name,&length,nullptr,0)==0)trace("sysctl name: %s",name);
  }
  trace("sysctl list: stopped at the limit");
}
// A test aid: the one console we have can stand in for one we don't. A word in this file
// can only mark a value as unread, which makes the app more careful and never less.
void apply_test_file(ui::Console& console) {
  StickAccess access;
  const char* root=usb_root(); if(!root)return;
  FILE* file=fopen((std::string(root)+"/DiagnostiX/simulate-console.txt").c_str(),"r"); if(!file)return;
  char buffer[256]={}; const size_t count=fread(buffer,1,sizeof(buffer)-1,file); fclose(file); buffer[count]=0;
  trace("test file: %s",buffer);
  if(strstr(buffer,"southbridge"))console.southbridge_known=false;
  if(strstr(buffer,"firmware"))console.firmware_known=false;
  if(strstr(buffer,"sysctl-list"))list_sysctl_names();
}
// Written by Linux as tests run, so a run that never finished is visible here.
void refresh_last_run(ui::HomeView& home) {
  home.last=ui::LastRun::None; home.last_stage.clear();
  StickAccess access;
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
enum class Page { Home, About, CleanupReview, CleanupConfirm, NoUsb, TryLinux, Starting };
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
  auto result=bundle.inspect(); bool ready=result==Result::Ready, handoff_attempted=false, manual=false, untested=false;
  std::string status=bundle.status(result); diagnostix::CleanupPreview cleanup; size_t review_page=0;
  ui::HomeView home; home.console=read_console(); apply_test_file(home.console); home.files=files_state(result);
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
    mark_started();
    const std::string extra=untested?ui::console_argument(home.console):std::string();
    trace("handing over to the loader: %s",extra.empty()?"tested console":extra.c_str());
    launch_boot(bundle,result,ready,handoff_attempted,status,manual,extra);
    trace("handover returned: %s",status.c_str());
    if(result==Result::Sent){page=Page::Starting;}
    else{clear_started();home.problem=status;home.files=files_state(result);page=Page::Home;choice=0;}
  };
  for(;;) {
    if(frames++%120==0){
      StickAccess access;
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
      case Page::TryLinux: ui::draw_try_linux(canvas,faces,choice); break;
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
          case ui::Action::TryLinux: page=Page::TryLinux;choice=0; break;
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
      } else if(page==Page::TryLinux) {
        if(choice==1) {
          manual=false;untested=true;choice=0;
          trace("chosen: try Linux anyway, usb=%d",home.usb);
          if(home.usb)start(); else page=Page::NoUsb;
        } else {page=Page::Home;choice=0;}
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
