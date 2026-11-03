"""Compile actual vendor-parser fixes against fixed protocol and buffer mocks."""
import unittest

from boot_defaults_test import ROOT, function, postimages
from platform_failure_test import run_c

PATCH = ROOT / "platforms/ps4/patches/7.2-ps4-baikal/0500-network-mt7668/0001-mt76x8-bound-config-command-inputs.patch"
BASE = "drivers/net/wireless/mediatek/mt76x8/drv_wlan/MT6632/wlan/"


class WifiConfigTests(unittest.TestCase):
    def test_proc_prefix_payload_and_termination(self):
        text = postimages(PATCH)[BASE + "os/linux/gl_proc.c"]
        source = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stddef.h>
#include <stdio.h>
#include <string.h>
#include <errno.h>
#define __user
#define DBGLOG(...) ((void)0)
#define kalMemSet memset
#define scnprintf snprintf
typedef long ssize_t;
typedef long loff_t;
typedef unsigned int UINT_32;
typedef int INT_32;
typedef char *PUINT_8;
struct file { int unused; };
struct glue { void *prDevHandler; };
typedef struct glue *P_GLUE_INFO_T;
static struct glue glue;
static P_GLUE_INFO_T g_prGlueInfo_proc = &glue;
static char g_aucProcBuf[3000];
static int g_u4NextDriverReadLen;
static size_t copied;
static unsigned int calls;
static bool fail_copy;
static int copy_from_user(void *dst, const void *src, size_t size)
{
    assert((char *)dst >= g_aucProcBuf);
    assert((char *)dst + size < g_aucProcBuf + sizeof(g_aucProcBuf));
    copied = size;
    if (fail_copy) return 1;
    memcpy(dst, src, size);
    return 0;
}
static int priv_driver_set_cfg(void *dev, char *buffer, size_t size)
{ assert(buffer == g_aucProcBuf && size == sizeof(g_aucProcBuf)); calls++; return 0; }
'''
        source += function(text, "procCfgWrite")
        source += r'''
int main(void)
{
    char input[3000];
    size_t sizes[] = {1, 3, 7, 8, 32, 2991, 2992, 3000, SIZE_MAX};
    memset(input, 'x', sizeof(input));
    assert(procCfgWrite(NULL,input,0,NULL) == 0 && calls == 0);
    for (unsigned int i = 0; i < sizeof(sizes)/sizeof(sizes[0]); i++) {
        size_t expected = sizes[i] < 2991 ? sizes[i] : 2991;
        calls = 0;
        procCfgWrite(NULL,input,sizes[i],NULL);
        assert(calls == 1 && copied == expected);
        assert(!memcmp(g_aucProcBuf,"set_cfg ",8));
        assert(!memcmp(g_aucProcBuf + 8,input,expected));
        assert(g_aucProcBuf[8 + expected] == 0);
    }
    calls = 0; fail_copy = true;
    assert(procCfgWrite(NULL,input,3,NULL) == -EFAULT && calls == 0);
    return 0;
}
'''
        run_c(source)

    def test_firmware_parser_rejects_bad_lengths_and_item_indexes(self):
        text = postimages(PATCH)[BASE + "common/wlan_lib.c"]
        source = r'''
#include <assert.h>
#include <stdint.h>
#include <stddef.h>
#include <string.h>
#define DBGLOG(...) ((void)0)
#define kalStrnCpy(dst,src,n) strncpy((char *)(dst),(const char *)(src),(n))
#define ED_STRING_SITE 0
#define ED_VALUE_SITE 1
#define ITEM_TYPE_DEC 1
#define ITEM_TYPE_HEX 2
#define ITEM_TYPE_STR 3
#define MAX_CMD_ITEM_MAX 4
#define WLAN_STATUS_FAILURE 1
#define WLAN_STATUS_SUCCESS 0
typedef char *PCHAR;
typedef char *PUCHAR;
typedef unsigned char UCHAR;
typedef unsigned int UINT_32;
typedef int INT_32;
typedef int WLAN_STATUS;
typedef struct {
    uint8_t itemType, itemStringLength, itemValueLength, reserved;
    uint8_t itemString[32], itemValue[32];
} CMD_FORMAT_V1_T, *P_CMD_FORMAT_V1_T;
static int byte_digit(char c) { return c - '0'; }
#define atoi byte_digit
'''
        source += function(text, "wlanCfgParseToFW")
        source += r'''
int main(void)
{
    char key[256], value[] = "123", lengths[] = {3,3};
    char *args[] = {key,value};
    CMD_FORMAT_V1_T output[4];
    memset(key,'k',sizeof(key));
    memset(output,0,sizeof(output));
    assert(wlanCfgParseToFW(args,lengths,2,(char *)output,0) == 0);
    assert(output[0].itemStringLength == 3 && output[0].itemValueLength == 1);
    assert(!memcmp(output[0].itemString,"kkk",3) && output[0].itemValue[0] == 123);
    lengths[0] = 32;
    assert(wlanCfgParseToFW(args,lengths,2,(char *)output,3) == 0);
    lengths[0] = 33;
    assert(wlanCfgParseToFW(args,lengths,2,(char *)output,0) == 1);
    lengths[0] = (char)255;
    assert(wlanCfgParseToFW(args,lengths,2,(char *)output,0) == 1);
    lengths[0] = 0;
    assert(wlanCfgParseToFW(args,lengths,2,(char *)output,0) == 1);
    lengths[0] = 3; lengths[1] = 33;
    assert(wlanCfgParseToFW(args,lengths,2,(char *)output,0) == 1);
    lengths[1] = 3;
    assert(wlanCfgParseToFW(args,lengths,2,(char *)output,4) == 1);
    assert(wlanCfgParseToFW(args,lengths,1,(char *)output,0) == 1);
    assert(wlanCfgParseToFW(NULL,lengths,2,(char *)output,0) == 1);
    assert(wlanCfgParseToFW(args,NULL,2,(char *)output,0) == 1);
    args[0] = NULL;
    assert(wlanCfgParseToFW(args,lengths,2,(char *)output,0) == 1);
    return 0;
}
'''
        run_c(source)


if __name__ == "__main__":
    unittest.main()
