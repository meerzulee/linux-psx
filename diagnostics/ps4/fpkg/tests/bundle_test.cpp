// SPDX-License-Identifier: MIT
#include "bundle.hpp"
#include "sha256.hpp"
#include <array>
#include <cassert>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <string>
#include <map>
#include <vector>
#include <sys/stat.h>
#include <unistd.h>
namespace fs=std::filesystem;
using diagnostix::Bundle; using diagnostix::Result;
constexpr const char* names[]={"bzImage","initramfs.cpio.gz","bootargs.txt","vram.txt","loader.elf","bundle.json"};
void write(const fs::path& p,const std::string& text){std::ofstream f(p,std::ios::binary);f<<text;assert(f.good());}
std::string read(const fs::path& p){std::ifstream f(p,std::ios::binary);return {std::istreambuf_iterator<char>(f),{}};}
struct Inputs{
  std::array<std::string,6> hashes;
  std::array<diagnostix::Pin,6> pins;
  fs::path path;
  Inputs(const fs::path& dir,char generation,const std::string& version="",const std::string& loader_extra=""):path(dir){
    fs::create_directory(path);
    for(size_t i=0;i<6;++i){std::string data(i==1?300001:81,generation);data+=names[i];
      if(!version.empty()){
        if(i==2)data="console=ttyS0\n";
        if(i==3)data="1024\n";
        if(i==4)data+=loader_extra;
        if(i==5){data="{\"schema\":1,\"product\":\"Diagnostix\",\"version\":\""+version+"\",\"files\":{";
          for(size_t j=0;j<5;++j){if(j)data+=",";data+="\""+std::string(names[j])+"\":{\"bytes\":"+std::to_string(pins[j].bytes)+",\"sha256\":\""+hashes[j]+"\"}";}data+="}}";}
      }
      write(path/names[i],data);
      uint8_t bytes[32];omarchy_ps4::sha256(reinterpret_cast<const uint8_t*>(data.data()),data.size(),bytes);char h[65];
      for(size_t j=0;j<32;++j)snprintf(h+j*2,3,"%02x",bytes[j]);hashes[i]=h;pins[i]={names[i],data.size(),hashes[i].c_str()};}
  }
  Bundle instance(const fs::path& root,diagnostix::Progress progress=nullptr,void* context=nullptr){Bundle b(pins.data(),pins.size(),progress,context);b.paths(path,root);return b;}
};
std::string hash(const std::string& data){uint8_t bytes[32];omarchy_ps4::sha256(reinterpret_cast<const uint8_t*>(data.data()),data.size(),bytes);char h[65];for(size_t i=0;i<32;++i)snprintf(h+i*2,3,"%02x",bytes[i]);return h;}
void package(const fs::path& p,const std::string& id="IV0000-DGNX00001_00-DIAGNOSTIX000001"){
  std::string header(0x70,'\0');header.replace(0,4,"\177CNT",4);header.replace(0x40,id.size(),id);write(p,header);fs::resize_file(p,(p.filename()=="diagnostix-ram-alpha1-0.03.pkg"||p.filename()=="diagnostix-ram-alpha1-0.04.pkg"||p.filename()=="diagnostix-ram-alpha1-0.05.pkg"||p.filename()=="diagnostix-ram-alpha1-0.06.pkg"||p.filename()=="diagnostix-ram-alpha1-0.07.pkg")?460193792:(p.filename()=="diagnostix-ram-alpha1-0.08.pkg"||p.filename()=="diagnostix-ram-alpha1-0.09.pkg"||p.filename()=="diagnostix-ram-alpha1-0.10.pkg"?460390400:460128256));
}
uint64_t payload_size(const Inputs& source){uint64_t bytes=0;for(const auto& pin:source.pins)bytes+=pin.bytes;return bytes;}
fs::path retained(const fs::path& root,const Inputs& source,const std::string& manifest=""){
  const auto raw=manifest.empty()?read(source.path/"bundle.json"):manifest;
  auto dir=root/("previous-"+hash(raw));fs::copy(source.path,dir,fs::copy_options::recursive);write(dir/"bundle.json",raw);return dir;
}
struct ProgressLog {
  std::map<unsigned,int> image_reads;
  std::vector<unsigned> phases;
  uint64_t last=0; unsigned complete=0, corrupt_step=0;
  fs::path mutation;
  fs::path stage;
  ino_t staged_inode=0;
  static void capture(const diagnostix::ProgressUpdate& update,void* context){
    auto& log=*static_cast<ProgressLog*>(context);
    assert(update.step>=1&&update.step<=update.steps&&update.phase_done<=update.phase_total);
    const uint64_t overall=update.complete?update.phase_total*update.steps:update.phase_total*(update.step-1)+update.phase_done;
    assert(overall>=log.last);log.last=overall;
    if(update.complete)++log.complete;
    if(!*update.file&&!update.complete){
      log.phases.push_back(update.step);
      if(update.step==log.corrupt_step){auto data=read(log.mutation);assert(!data.empty());data[0]^=1;write(log.mutation,data);log.corrupt_step=0;}
      if(update.step==3&&!log.stage.empty()){struct stat s{};assert(stat(log.stage.c_str(),&s)==0);log.staged_inode=s.st_ino;}
    }
    if(std::string(update.file)=="initramfs.cpio.gz"&&update.file_total&&update.file_done==update.file_total)++log.image_reads[update.step];
  }
};
int main(int argc,char**argv){assert(argc==2);fs::path home=argv[1];fs::create_directory(home);
  Inputs a(home/"source-a",'a'), b(home/"source-b",'b');
  {auto root=home/"fresh";auto app=a.instance(root);assert(app.inspect()==Result::Missing);assert(!fs::exists(root));
   assert(app.install()==Result::Ready);assert(app.inspect()==Result::Ready);assert(app.install()==Result::Ready);
   assert(app.boot()==Result::Sent);assert(app.boot()==Result::AlreadyAttempted);assert(app.handoffs==1);
   write(root/"boot/bzImage","damaged");auto changed=a.instance(root);assert(changed.boot()==Result::Invalid);assert(changed.handoffs==0);}
  std::cout<<"fresh, idempotence, reverify, one handoff PASS\n";
  {
    using diagnostix::redirect_loader_arguments;
    const std::string from="/a/boot/bootargs.txt",to="/a/manual.txt";
    std::string once=std::string("head\0",5)+from+std::string("\0tail",5);
    assert(redirect_loader_arguments(reinterpret_cast<uint8_t*>(once.data()),once.size(),from,to));
    assert(once==std::string("head\0",5)+to+std::string(from.size()-to.size(),'\0')+std::string("\0tail",5));
    std::string twice=from+std::string(1,'\0')+from+std::string(1,'\0');
    const std::string before=twice;
    assert(!redirect_loader_arguments(reinterpret_cast<uint8_t*>(twice.data()),twice.size(),from,to)&&twice==before);
    std::string missing="no path here";
    assert(!redirect_loader_arguments(reinterpret_cast<uint8_t*>(missing.data()),missing.size(),from,to));
    std::string longer=from+std::string(1,'\0');
    assert(!redirect_loader_arguments(reinterpret_cast<uint8_t*>(longer.data()),longer.size(),from,from+"x"));
    std::string prefix=from+"x"+std::string(1,'\0');
    assert(!redirect_loader_arguments(reinterpret_cast<uint8_t*>(prefix.data()),prefix.size(),from,to));

    const auto base=home/"manual-new",root=base/"versions/0.16";
    const std::string path=(root/"boot/bootargs.txt").string(),manual=(base/"manual-bootargs.txt").string();
    const std::string embedded=path+std::string(1,'\0');
    Inputs source(home/"manual-source",'m',"0.16",embedded);
    const auto loader=read(source.path/"loader.elf");
    Bundle automatic(source.pins.data(),source.pins.size());automatic.paths(source.path,root,base);
    assert(automatic.install()==Result::Ready&&automatic.boot()==Result::Sent&&automatic.sent==loader);
    assert(!fs::exists(manual));
    Bundle chosen(source.pins.data(),source.pins.size());chosen.paths(source.path,root,base);
    assert(chosen.inspect()==Result::Ready&&chosen.boot(true)==Result::Sent&&chosen.handoffs==1);
    assert(read(manual)=="console=ttyS0 dx.mode=manual");
    assert(chosen.sent.find(path)==std::string::npos&&chosen.sent.find(manual)!=std::string::npos);
    assert(chosen.sent.size()==loader.size()&&read(root/"boot/loader.elf")==loader);
    Bundle again(source.pins.data(),source.pins.size());again.paths(source.path,root,base);
    assert(again.inspect()==Result::Ready&&"the arguments file must not fail the install check");
    assert(again.boot(true)==Result::Sent&&read(manual)=="console=ttyS0 dx.mode=manual");
    Bundle named(source.pins.data(),source.pins.size());named.paths(source.path,root,base);
    assert(named.inspect()==Result::Ready&&named.boot(false,"dx.console=Belize-B0")==Result::Sent);
    assert(read(manual)=="console=ttyS0 dx.console=Belize-B0"&&named.sent.find(manual)!=std::string::npos);

    const auto other_base=home/"manual-absent",other_root=other_base/"versions/0.16";
    Inputs absent(home/"manual-absent-source",'n',"0.16");
    Bundle refused(absent.pins.data(),absent.pins.size());refused.paths(absent.path,other_root,other_base);
    assert(refused.install()==Result::Ready&&refused.boot(true)==Result::IoError&&refused.handoffs==0);
  }
  std::cout<<"manual arguments, unchanged automatic loader PASS\n";
  {
    Inputs source(home/"versioned-source",'v',"0.16");
    const auto base=home/"versioned-new",root=base/"versions/0.16";
    Bundle app(source.pins.data(),source.pins.size());app.paths(source.path,root,base);
    assert(app.inspect()==Result::Missing&&!fs::exists(base));
    assert(app.install()==Result::Ready&&app.inspect()==Result::Ready);
    assert(read(base/".owner")=="Diagnostix namespace v1\n");
    assert(read(base/"versions/.owner")==read(base/".owner"));
    assert(read(root/"boot/vram.txt")=="1024\n"&&!fs::exists(base/"boot"));
    assert(app.boot()==Result::Sent&&app.handoffs==1);
    // Legacy boot generations and unrelated sibling versions stay intact.
    const auto legacy_base=home/"versioned-legacy";
    auto legacy=a.instance(legacy_base);assert(legacy.install()==Result::Ready);
    const auto old_boot=read(legacy_base/"boot/bzImage"),old_owner=read(legacy_base/".owner");
    Bundle next(source.pins.data(),source.pins.size());next.paths(source.path,legacy_base/"versions/0.16",legacy_base);
    assert(next.install()==Result::Ready);
    fs::create_directory(legacy_base/"versions/0.11");write(legacy_base/"versions/0.11/keep","old version");
    fs::create_directory(legacy_base/"versions/0.16");write(legacy_base/"versions/0.16/keep","future version");
    assert(next.install()==Result::Ready);
    assert(read(legacy_base/"boot/bzImage")==old_boot&&read(legacy_base/".owner")==old_owner);
    assert(read(legacy_base/"versions/0.11/keep")=="old version"&&read(legacy_base/"versions/0.16/keep")=="future version");
    // Only two declared parents can be created; missing anchor and a different
    // version spelling never trigger generic recursive directory creation.
    for(const auto& rejected:{home/"missing-anchor/base",home/"wrong-version-base"}){
      Bundle bad(source.pins.data(),source.pins.size());
      auto child=rejected/"versions/0.16";
      if(rejected.filename()=="wrong-version-base")child=rejected/"versions/0.17";
      bad.paths(source.path,child,rejected);assert(bad.install()==Result::Invalid&&!fs::exists(rejected));
    }
    // Refuse foreign ownership at each ancestor without modifying its files.
    auto foreign=home/"versioned-foreign";fs::create_directory(foreign);write(foreign/".owner","foreign owner\n");
    Bundle bad(source.pins.data(),source.pins.size());bad.paths(source.path,foreign/"versions/0.16",foreign);
    assert(bad.install()==Result::Invalid&&read(foreign/".owner")=="foreign owner\n"&&!fs::exists(foreign/"versions"));
    auto parent_foreign=home/"versioned-foreign-parent";auto old=a.instance(parent_foreign);assert(old.install()==Result::Ready);
    fs::create_directory(parent_foreign/"versions");write(parent_foreign/"versions/.owner","foreign versions\n");
    bad.paths(source.path,parent_foreign/"versions/0.16",parent_foreign);
    assert(bad.install()==Result::Invalid&&!fs::exists(parent_foreign/"versions/0.16"));
    assert(read(parent_foreign/"versions/.owner")=="foreign versions\n");
    // Base, versions and final-root symlinks cannot redirect preparation/logs.
    auto alias=home/"versioned-base-alias";fs::create_directory_symlink(legacy_base,alias);
    const auto log_before=read(legacy_base/"versions/0.16/launcher-status.log");
    bad.paths(source.path,alias/"versions/0.16",alias);assert(bad.install()==Result::Invalid);
    assert(read(legacy_base/"versions/0.16/launcher-status.log")==log_before);
    auto versions_alias=home/"versioned-versions-alias";old=a.instance(versions_alias);assert(old.install()==Result::Ready);
    fs::create_directory_symlink(legacy_base/"versions",versions_alias/"versions");
    bad.paths(source.path,versions_alias/"versions/0.16",versions_alias);assert(bad.install()==Result::Invalid);
    assert(read(legacy_base/"versions/0.16/launcher-status.log")==log_before);
    auto root_alias=home/"versioned-root-alias";old=a.instance(root_alias);assert(old.install()==Result::Ready);
    fs::create_directory(root_alias/"versions");write(root_alias/"versions/.owner",old_owner);
    fs::create_directory_symlink(legacy_base/"versions/0.16",root_alias/"versions/0.16");
    bad.paths(source.path,root_alias/"versions/0.16",root_alias);assert(bad.install()==Result::Invalid);
    assert(read(legacy_base/"versions/0.16/launcher-status.log")==log_before);
  }
  std::cout<<"versioned namespace explicit parents, legacy/sibling preservation and ownership/symlink guards PASS\n";
  {auto root=home/"upgrade";auto old=a.instance(root);assert(old.install()==Result::Ready);auto next=b.instance(root);assert(next.install()==Result::Ready);
   auto previous=root/("previous-"+a.hashes[5]);assert(read(previous/"bzImage")==read(a.path/"bzImage"));assert(read(root/"boot/bzImage")==read(b.path/"bzImage"));}
  std::cout<<"upgrade preserves previous generation PASS\n";
  for(int point=1;point<=4;++point){auto root=home/("interrupted-"+std::to_string(point));auto old=a.instance(root);assert(old.install()==Result::Ready);
    std::ifstream held(root/"boot/bzImage",std::ios::binary);auto next=b.instance(root);next.failpoint=point;assert(next.install()==Result::IoError);assert(next.inspect()==Result::Interrupted);assert(next.boot()==Result::Invalid);
    std::string held_data{std::istreambuf_iterator<char>(held),{}};assert(held_data==read(a.path/"bzImage"));
    auto recovery=b.instance(root);auto result=recovery.install();assert(result==(point==3?Result::Ready:Result::Recovered));
    if(point!=3){assert(read(root/"boot/bzImage")==read(a.path/"bzImage"));assert(recovery.install()==Result::Ready);}assert(recovery.inspect()==Result::Ready);
  }
  std::cout<<"four interrupted commit boundaries and open old inode PASS\n";
  {auto root=home/"bad-source";auto app=a.instance(root);auto saved=read(a.path/"loader.elf");write(a.path/"loader.elf","corrupt");assert(app.install()==Result::Invalid);assert(!fs::exists(root));write(a.path/"loader.elf",saved);}
  {auto foreign=home/"foreign";fs::create_directory(foreign);write(foreign/"precious","keep");auto app=a.instance(foreign);assert(app.install()==Result::Invalid);assert(read(foreign/"precious")=="keep");}
  {auto target=home/"other-app";fs::create_directory(target);auto root=home/"alias";fs::create_directory_symlink(target,root);auto app=a.instance(root);assert(app.install()==Result::Invalid);assert(fs::is_empty(target));}
  {auto root=home/"file-alias";auto app=a.instance(root);assert(app.install()==Result::Ready);fs::remove(root/"boot/bzImage");fs::create_symlink(a.path/"bzImage",root/"boot/bzImage");assert(app.inspect()==Result::Invalid);assert(app.boot()==Result::Invalid);}
  {auto root=home/"unknown-member";auto app=a.instance(root);assert(app.install()==Result::Ready);write(root/"boot/foreign","keep");auto next=b.instance(root);assert(next.install()==Result::Invalid);assert(read(root/"boot/foreign")=="keep");}
  {auto root=home/"journal-alias";auto app=a.instance(root);assert(app.install()==Result::Ready);fs::create_symlink(home/"absent-journal",root/".transaction");assert(app.inspect()==Result::Interrupted);assert(app.boot()==Result::Invalid);assert(app.install()==Result::Invalid);}
  for(const std::string marker:{"", "Diagnostix name"}){auto root=home/(marker.empty()?"empty-initial":"partial-owner");fs::create_directory(root);if(!marker.empty())write(root/".owner",marker);auto app=a.instance(root);assert(app.inspect()==Result::Invalid);assert(app.install()==Result::Ready);}
  {auto root=home/"foreign-owner";fs::create_directory(root);write(root/".owner","another application");auto app=a.instance(root);assert(app.install()==Result::Invalid);assert(read(root/".owner")=="another application");}
  std::cout<<"corruption, foreign namespace, symlink and unknown member refusals PASS\n";
  {
    auto root=home/"progress-fresh";ProgressLog log;log.stage=root/"boot.new/initramfs.cpio.gz";
    auto app=a.instance(root,ProgressLog::capture,&log);assert(app.install()==Result::Ready);
    assert((log.image_reads==std::map<unsigned,int>{{2,1}}));
    assert((log.phases==std::vector<unsigned>{1,2,3}));assert(log.complete==1);
    struct stat s{};assert(stat((root/"boot/initramfs.cpio.gz").c_str(),&s)==0&&s.st_ino==log.staged_inode);
    log={};assert(app.inspect()==Result::Ready);assert(log.image_reads.empty());
    log={};assert(app.install()==Result::Ready);assert(log.image_reads.empty());assert(log.complete==1);
    log={};assert(app.boot()==Result::Sent);assert(log.image_reads.empty());assert(log.complete==1&&app.handoffs==1);
    assert(app.boot()==Result::AlreadyAttempted);assert(app.handoffs==1);
    // A new application launch establishes a new cache with exactly one read.
    log={};auto restarted=a.instance(root,ProgressLog::capture,&log);assert(restarted.inspect()==Result::Ready);
    assert((log.image_reads==std::map<unsigned,int>{{1,1}}));
    log={};assert(restarted.install()==Result::Ready);assert(log.image_reads.empty());
    log={};assert(restarted.boot()==Result::Sent);assert(log.image_reads.empty());
  }
  {
    auto root=home/"progress-upgrade";auto old=a.instance(root);assert(old.install()==Result::Ready);
    ProgressLog log;auto next=b.instance(root,ProgressLog::capture,&log);
    assert(next.inspect()==Result::Invalid&&log.image_reads.empty());log={};assert(next.install()==Result::Ready);
    assert((log.image_reads==std::map<unsigned,int>{{2,1}}));assert(log.complete==1);
  }
  {
    auto root=home/"progress-cache-change";ProgressLog log;auto app=a.instance(root,ProgressLog::capture,&log);assert(app.install()==Result::Ready);
    // Even a same-size metadata change blocks Boot without silently rehashing.
    write(root/"boot/bzImage",read(a.path/"bzImage"));log={};
    assert(app.boot()==Result::Invalid&&app.handoffs==0&&log.image_reads.empty());
    log={};assert(app.install()==Result::Ready);assert((log.image_reads==std::map<unsigned,int>{{1,1}}));
    log={};assert(app.boot()==Result::Sent&&app.handoffs==1&&log.image_reads.empty());
  }
  for(unsigned point:{2u,3u}){
    auto root=home/("progress-failure-"+std::to_string(point));auto old=a.instance(root);assert(old.install()==Result::Ready);
    Inputs replacement(home/("mutation-source-"+std::to_string(point)),'c');ProgressLog log;log.corrupt_step=point;
    log.mutation=point==2?replacement.path/"initramfs.cpio.gz":root/"boot.new/initramfs.cpio.gz";
    auto next=replacement.instance(root,ProgressLog::capture,&log);assert(next.install()==Result::IoError);assert(log.complete==0);
    assert((log.image_reads==std::map<unsigned,int>{{2,1}}));
    assert(read(root/"boot/initramfs.cpio.gz")==read(a.path/"initramfs.cpio.gz"));assert(fs::exists(root/".transaction"));
    auto blocked=replacement.instance(root);assert(blocked.boot()==Result::Invalid);assert(blocked.handoffs==0);
  }
  std::cout<<"single image pass, cached Install/Boot, one restart read, inode binding and failure guards PASS\n";
  {
    using diagnostix::CleanupPreview; using diagnostix::CleanupResult;
    Inputs old1(home/"cleanup-source1",'d',"0.01"),old2(home/"cleanup-source2",'e',"0.02"),old3(home/"cleanup-source3",'h',"0.03"),old4(home/"cleanup-source4",'i',"0.04"),old5(home/"cleanup-source5",'k',"0.05"),old6(home/"cleanup-source6",'l',"0.06"),old7(home/"cleanup-source7",'m',"0.07"),old8(home/"cleanup-source8",'n',"0.08"),old9(home/"cleanup-source9",'o',"0.09"),old10(home/"cleanup-source10",'p',"0.10"),current(home/"cleanup-current",'f',"0.16"),future(home/"cleanup-future",'g',"0.16");
    const auto pkg=home/"packages";fs::create_directory(pkg);
    const std::string p1="diagnostix-ram-alpha1-0.01.pkg",p2="diagnostix-ram-alpha1-0.02.pkg",p3="diagnostix-ram-alpha1-0.03.pkg",p4="diagnostix-ram-alpha1-0.04.pkg",p5="diagnostix-ram-alpha1-0.05.pkg",p6="diagnostix-ram-alpha1-0.06.pkg",p7="diagnostix-ram-alpha1-0.07.pkg",p8="diagnostix-ram-alpha1-0.08.pkg",p9="diagnostix-ram-alpha1-0.09.pkg",p10="diagnostix-ram-alpha1-0.10.pkg";
    package(pkg/p1);package(pkg/p2);package(pkg/p3);package(pkg/p4);package(pkg/p5);package(pkg/p6);package(pkg/p7);package(pkg/p8);package(pkg/p9);package(pkg/p10);package(pkg/"diagnostix-ram-alpha1-0.11.pkg");package(pkg/"diagnostix-ram-alpha1-0.14.pkg");package(pkg/"diagnostix-ram-alpha1-0.16.pkg");
    package(pkg/"foreign.pkg");write(pkg/"personal.txt","keep");
    CleanupPreview preview;uint64_t reclaimed=99;
    auto first=current.instance(home/"not-installed");first.package_path(pkg);
    assert(first.clear_old_files(&reclaimed)==CleanupResult::Blocked&&reclaimed==0);
    assert(first.preview_cleanup(&preview)==CleanupResult::Preview&&preview.items.size()==10&&preview.bytes==4602396672&&preview.generations_protected);
    assert(!fs::exists(home/"not-installed")&&fs::exists(pkg/p1)&&fs::exists(pkg/p2));
    assert(first.clear_old_files(&reclaimed)==CleanupResult::Cleared&&reclaimed==4602396672);
    assert(first.clear_old_files(&reclaimed)==CleanupResult::Blocked&&reclaimed==0);
    assert(fs::exists(pkg/"diagnostix-ram-alpha1-0.11.pkg")&&fs::exists(pkg/"diagnostix-ram-alpha1-0.14.pkg")&&fs::exists(pkg/"diagnostix-ram-alpha1-0.16.pkg")&&fs::exists(pkg/"foreign.pkg")&&read(pkg/"personal.txt")=="keep");
    auto root=home/"cleanup-owned";auto install=current.instance(root);install.package_path(pkg);assert(install.install()==Result::Ready);
    const auto previous1=retained(root,old1),previous2=retained(root,old2),previous3=retained(root,old3),previous4=retained(root,old4),previous5=retained(root,old5),previous6=retained(root,old6),previous7=retained(root,old7),previous8=retained(root,old8),previous9=retained(root,old9),previous10=retained(root,old10);
    const auto future_dir=retained(root,future),same=retained(root,current);
    const auto unknown=retained(root,old1,read(old1.path/"bundle.json")+"\n");write(unknown/"personal","keep");
    const auto alias=retained(root,old1,read(old1.path/"bundle.json")+"\n\n");fs::remove(alias/"loader.elf");fs::create_symlink(old1.path/"loader.elf",alias/"loader.elf");
    fs::create_directory_symlink(old2.path,root/("previous-"+std::string(64,'a')));
    const auto wrong_suffix=root/("previous-"+std::string(64,'b'));fs::copy(old1.path,wrong_suffix,fs::copy_options::recursive);
    std::string duplicate=read(old1.path/"bundle.json");duplicate.insert(1,"\"version\":\"0.14\",");const auto duplicate_dir=retained(root,old1,duplicate);
    auto invalid_size=retained(root,old1,read(old1.path/"bundle.json")+"\n\n\n");write(invalid_size/"bzImage","wrong size");
    auto linked=retained(root,old1,read(old1.path/"bundle.json")+"\n\n\n\n");fs::remove(linked/"loader.elf");fs::create_hard_link(old1.path/"loader.elf",linked/"loader.elf");
    auto unverified=current.instance(root);unverified.package_path(pkg);assert(unverified.preview_cleanup(&preview)==CleanupResult::Empty&&preview.generations_protected);
    package(pkg/p1);package(pkg/p2);package(pkg/p3);package(pkg/p4);package(pkg/p5);package(pkg/p6);package(pkg/p7);package(pkg/p8);package(pkg/p9);package(pkg/p10);
    assert(install.preview_cleanup(&preview)==CleanupResult::Preview&&preview.items.size()==20&&!preview.generations_protected);
    const uint64_t expected=4602396672+payload_size(old1)+payload_size(old2)+payload_size(old3)+payload_size(old4)+payload_size(old5)+payload_size(old6)+payload_size(old7)+payload_size(old8)+payload_size(old9)+payload_size(old10);assert(preview.bytes==expected);
    const auto active=read(root/"boot/bundle.json"),marker=read(root/".owner");
    assert(install.clear_old_files(&reclaimed)==CleanupResult::Cleared&&reclaimed==expected);
    assert(!fs::exists(previous1)&&!fs::exists(previous2)&&!fs::exists(previous3)&&!fs::exists(previous4)&&!fs::exists(previous5)&&!fs::exists(previous6)&&!fs::exists(previous7)&&!fs::exists(previous8)&&!fs::exists(previous9)&&!fs::exists(previous10)&&!fs::exists(pkg/p1)&&!fs::exists(pkg/p2)&&!fs::exists(pkg/p3)&&!fs::exists(pkg/p4)&&!fs::exists(pkg/p5)&&!fs::exists(pkg/p6)&&!fs::exists(pkg/p7)&&!fs::exists(pkg/p8)&&!fs::exists(pkg/p9)&&!fs::exists(pkg/p10));
    assert(read(root/"boot/bundle.json")==active&&read(root/".owner")==marker);
    for(const auto& keep:{future_dir,same,unknown,alias,wrong_suffix,duplicate_dir,invalid_size,linked})assert(fs::exists(keep));
    // A replaced file, same-size active edit, new journal/stage, or handoff
    // invalidates confirmation before the first unlink.
    for(const std::string barrier:{".transaction",".transaction.new","boot.new"}){
      package(pkg/p1);assert(install.preview_cleanup(&preview)==CleanupResult::Preview);
      if(barrier=="boot.new")fs::create_directory(root/barrier);else fs::create_symlink(home/"absent-cleanup-journal",root/barrier);
      assert(install.clear_old_files(&reclaimed)==CleanupResult::Blocked&&reclaimed==0&&fs::exists(pkg/p1));
      assert(install.preview_cleanup(&preview)==CleanupResult::Blocked);fs::remove(root/barrier);
    }
    assert(install.preview_cleanup(&preview)==CleanupResult::Preview);package(pkg/p1,"IV0000-FOREIGN01_00-DIAGNOSTIX000001");
    assert(install.clear_old_files(&reclaimed)==CleanupResult::Changed&&reclaimed==0&&fs::exists(pkg/p1));
    fs::remove(pkg/p1);fs::create_symlink(pkg/"diagnostix-ram-alpha1-0.09.pkg",pkg/p1);
    assert(install.preview_cleanup(&preview)==CleanupResult::Empty&&fs::is_symlink(pkg/p1));fs::remove(pkg/p1);
    package(pkg/p1);fs::resize_file(pkg/p1,460128255);assert(install.preview_cleanup(&preview)==CleanupResult::Empty);fs::remove(pkg/p1);
    const auto extra=retained(root,old2);assert(install.preview_cleanup(&preview)==CleanupResult::Preview);
    auto changed=read(root/"boot/bzImage");changed[0]^=1;write(root/"boot/bzImage",changed);
    assert(install.clear_old_files(&reclaimed)==CleanupResult::Changed&&reclaimed==0&&fs::exists(extra));
    write(root/"boot/bzImage",read(current.path/"bzImage"));assert(install.inspect()==Result::Ready);
    assert(install.preview_cleanup(&preview)==CleanupResult::Preview);assert(install.boot()==Result::Sent);
    assert(install.clear_old_files(&reclaimed)==CleanupResult::Blocked&&reclaimed==0&&fs::exists(extra));
    assert(install.preview_cleanup(&preview)==CleanupResult::Blocked);
    // An unowned root never authorizes deleting retained generations.
    auto foreign_root=home/"cleanup-foreign";fs::create_directory(foreign_root);write(foreign_root/".owner","foreign\n");const auto foreign_old=retained(foreign_root,old1);
    auto foreign_app=current.instance(foreign_root);foreign_app.package_path(pkg);assert(foreign_app.preview_cleanup(&preview)==CleanupResult::Empty&&fs::exists(foreign_old));
    // A changed member after preview is preserved rather than partially removed.
    auto changed_root=home/"cleanup-changed";auto changed_app=current.instance(changed_root);changed_app.package_path(pkg);assert(changed_app.install()==Result::Ready);auto changed_old=retained(changed_root,old1);
    assert(changed_app.preview_cleanup(&preview)==CleanupResult::Preview);write(changed_old/"personal","keep");
    assert(changed_app.clear_old_files(&reclaimed)==CleanupResult::Changed&&reclaimed==0&&read(changed_old/"personal")=="keep");
    // A directory alias cannot redirect the known installer names.
    auto pkg_alias=home/"package-alias";fs::create_directory_symlink(pkg,pkg_alias);changed_app.package_path(pkg_alias);assert(changed_app.preview_cleanup(&preview)==CleanupResult::Empty);
    // More than the bounded preview capacity refuses the whole cleanup.
    auto bounded_root=home/"cleanup-bounded";auto bounded=current.instance(bounded_root);bounded.package_path(pkg);assert(bounded.install()==Result::Ready);
    for(size_t i=0;i<33;++i)retained(bounded_root,old2,read(old2.path/"bundle.json")+std::string(i,' '));
    assert(bounded.preview_cleanup(&preview)==CleanupResult::Blocked&&preview.items.empty());
    assert(bounded.clear_old_files(&reclaimed)==CleanupResult::Blocked&&reclaimed==0);
    // An interrupted cleanup reports only completed unlinks and leaves an
    // incomplete generation alone on every subsequent preview.
    auto partial_root=home/"cleanup-partial";auto partial=current.instance(partial_root);partial.package_path(pkg);assert(partial.install()==Result::Ready);
    auto partial_old=retained(partial_root,old1);assert(partial.preview_cleanup(&preview)==CleanupResult::Preview);partial.failpoint=5;
    assert(partial.clear_old_files(&reclaimed)==CleanupResult::IoError&&reclaimed==old1.pins[0].bytes);
    assert(!fs::exists(partial_old/"bzImage")&&fs::exists(partial_old/"initramfs.cpio.gz"));write(partial_old/"personal","keep");
    assert(partial.preview_cleanup(&preview)==CleanupResult::Empty&&read(partial_old/"personal")=="keep");
  }
  std::cout<<"cleanup preview, exact reclaimed bytes, ownership/version/alias guards and transaction/handoff refusals PASS\n";
  {
    Inputs source(home/"logging-source",'j',"0.16");auto root=home/"logging-owned";auto app=source.instance(root);
    fs::create_hard_link(source.path/"loader.elf",home/"read-only-source-alias");
    assert(app.install()==Result::Ready);auto log=read(root/"launcher-status.log");
    assert(log.find("Install / Verify selected; entry")!=std::string::npos&&log.find("source-stat:")!=std::string::npos&&log.find("nlink=2")!=std::string::npos&&log.find("shape-eof:")!=std::string::npos);
    const auto active=read(root/"boot/bundle.json");const auto saved=read(source.path/"loader.elf");write(source.path/"loader.elf","bad size");
    assert(app.install()==Result::Invalid&&app.status(Result::Invalid).find("Source preflight: loader.elf: size expected")!=std::string::npos);
    assert(read(root/"boot/bundle.json")==active&&!fs::exists(root/".transaction"));
    log=read(root/"launcher-status.log");assert(log.find("size expected")!=std::string::npos&&log.find("mode=")!=std::string::npos);
    write(source.path/"loader.elf",saved);for(int i=0;i<12;++i)assert(app.install()==Result::Ready);
    assert(fs::file_size(root/"launcher-status.log")<=8192);
    fs::create_hard_link(root/"boot/bzImage",home/"writable-output-alias");assert(app.boot()==Result::Invalid&&app.handoffs==0);
    auto foreign=home/"foreign-log-target";write(foreign,"keep");fs::remove(root/"launcher-status.log");fs::create_symlink(foreign,root/"launcher-status.log");
    assert(app.install()==Result::Ready);assert(read(foreign)=="keep"&&fs::is_symlink(root/"launcher-status.log"));
  }
  std::cout<<"PFS source link tolerance, strict writable identity, precise failures and bounded owned log PASS\n";

}
