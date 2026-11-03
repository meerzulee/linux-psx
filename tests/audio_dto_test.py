"""Exercise the actual Baikal modeset function's audio clock writes on CPU."""
import unittest

from boot_defaults_test import ROOT, function, postimages
from platform_failure_test import run_c

PATCH = ROOT / "platforms/ps4/patches/7.2-ps4-baikal/0300-gpu-liverpool/0038-amdgpu-ps4-program-audio-dto.patch"


class AudioDtoTests(unittest.TestCase):
    def test_asic_gate_clock_source_and_invalid_modes(self):
        text = postimages(PATCH)["drivers/gpu/drm/amd/amdgpu/dce_v8_0.c"]
        source = r'''
#include <assert.h>
#include <stdint.h>
#include <stddef.h>
#include <errno.h>
#define CHIP_LIVERPOOL 1
#define CHIP_GLADIUS 2
#define mmDCCG_AUDIO_DTO_SOURCE 0
#define mmDCCG_AUDIO_DTO0_PHASE 1
#define mmDCCG_AUDIO_DTO0_MODULE 2
#define DCCG_AUDIO_DTO_SOURCE__DCCG_AUDIO_DTO0_SOURCE_SEL__SHIFT 0
#define pr_info(...) ((void)0)
#define pr_warn(...) ((void)0)
struct drm_display_mode { int clock; };
struct amdgpu_device { int asic_type; uint32_t registers[3]; unsigned int writes; };
struct drm_device { struct amdgpu_device *adev; };
struct amdgpu_crtc { unsigned int adjusted_clock, crtc_id; struct drm_display_mode hw_mode; };
struct drm_crtc { struct drm_device *dev; struct amdgpu_crtc *private; };
struct drm_framebuffer { int unused; };
#define to_amdgpu_crtc(crtc) ((crtc)->private)
#define drm_to_adev(dev) ((dev)->adev)
static void write_reg(struct amdgpu_device *adev, unsigned int reg, uint32_t value)
{ assert(reg < 3); adev->registers[reg] = value; adev->writes++; }
#define WREG32(reg,value) write_reg(adev,(reg),(value))
#define amdgpu_atombios_crtc_set_pll(...) ((void)0)
#define amdgpu_atombios_crtc_set_dtd_timing(...) ((void)0)
#define dce_v8_0_crtc_do_set_base(...) ((void)0)
#define amdgpu_atombios_crtc_overscan_setup(...) ((void)0)
#define amdgpu_atombios_crtc_scaler_setup(...) ((void)0)
#define dce_v8_0_cursor_reset(...) ((void)0)
'''
        source += function(text, "dce_v8_0_crtc_mode_set")
        source += r'''
int main(void)
{
    struct amdgpu_device gpu = { .asic_type = CHIP_LIVERPOOL };
    struct drm_device dev = { .adev = &gpu };
    struct amdgpu_crtc private = { .adjusted_clock = 148500, .crtc_id = 0 };
    struct drm_crtc crtc = { .dev = &dev, .private = &private };
    struct drm_display_mode mode = { .clock = 148500 };
    assert(dce_v8_0_crtc_mode_set(&crtc,&mode,&mode,0,0,NULL) == 0);
    assert(gpu.writes == 3 && gpu.registers[0] == 0);
    assert(gpu.registers[1] == 24000 && gpu.registers[2] == 148500);
    gpu.asic_type = CHIP_GLADIUS; gpu.writes = 0;
    private.crtc_id = 1; mode.clock = 74250;
    assert(dce_v8_0_crtc_mode_set(&crtc,&mode,&mode,0,0,NULL) == 0);
    assert(gpu.writes == 3 && gpu.registers[0] == 1);
    assert(gpu.registers[2] == 74250);
    gpu.asic_type = 99; gpu.writes = 0;
    assert(dce_v8_0_crtc_mode_set(&crtc,&mode,&mode,0,0,NULL) == 0 && !gpu.writes);
    gpu.asic_type = CHIP_LIVERPOOL; private.adjusted_clock = 0;
    mode.clock = 148500;
    assert(dce_v8_0_crtc_mode_set(&crtc,&mode,&mode,0,0,NULL) == 0);
    assert(private.adjusted_clock == 148500 && gpu.writes == 3);
    gpu.writes = 0; mode.clock = 0; private.adjusted_clock = 0;
    assert(dce_v8_0_crtc_mode_set(&crtc,&mode,&mode,0,0,NULL) == -EINVAL && !gpu.writes);
    private.adjusted_clock = 148500;
    assert(dce_v8_0_crtc_mode_set(&crtc,&mode,&mode,0,0,NULL) == 0 && !gpu.writes);
    mode.clock = -1;
    assert(dce_v8_0_crtc_mode_set(&crtc,&mode,&mode,0,0,NULL) == 0 && !gpu.writes);
    return 0;
}
'''
        run_c(source)


if __name__ == "__main__":
    unittest.main()
