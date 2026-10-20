// SPDX-License-Identifier: MIT
/* Include after upstream cube_functions.h. Preserve real function pointers;
 * trace client calls and stop on an unsuccessful bounded fence/acquire wait.
 * No reset, forced completion, retries or system installation.
 */
static void psx_trace_maps(void)
{
    FILE *file = fopen("/proc/self/maps", "r");
    char line[2048];
    if (!file) return;
    while (fgets(line, sizeof(line), file))
        if (strstr(line, "libvulkan_radeon")) fprintf(stderr, "PSX_MAP %s", line);
    fclose(file);
}

static VkResult psx_create_device(VkPhysicalDevice gpu, const VkDeviceCreateInfo *info,
                                  const VkAllocationCallbacks *allocator, VkDevice *device)
{
    fprintf(stderr, "PSX_BEGIN create_device\n"); fflush(stderr);
    VkResult result = vkCreateDevice(gpu, info, allocator, device);
    fprintf(stderr, "PSX_END create_device result=%d\n", result); fflush(stderr);
    psx_trace_maps();
    return result;
}

static VkResult psx_swapchain(VkDevice device, const VkSwapchainCreateInfoKHR *info,
                              const VkAllocationCallbacks *allocator, VkSwapchainKHR *swapchain)
{
    fprintf(stderr, "PSX_BEGIN create_swapchain mode=%u format=%u width=%u height=%u\n",
            info->presentMode, info->imageFormat, info->imageExtent.width, info->imageExtent.height);
    fflush(stderr);
    VkResult result = vkCreateSwapchainKHR(device, info, allocator, swapchain);
    fprintf(stderr, "PSX_END create_swapchain result=%d\n", result); fflush(stderr);
    return result;
}

static VkResult psx_submit(VkQueue queue, uint32_t count, const VkSubmitInfo *info, VkFence fence)
{
    fprintf(stderr, "PSX_BEGIN submit count=%u waits=%u commands=%u signals=%u\n", count,
            count ? info->waitSemaphoreCount : 0, count ? info->commandBufferCount : 0,
            count ? info->signalSemaphoreCount : 0); fflush(stderr);
    VkResult result = vkQueueSubmit(queue, count, info, fence);
    fprintf(stderr, "PSX_END submit result=%d\n", result); fflush(stderr);
    return result;
}

static VkResult psx_wait(VkDevice device, uint32_t count, const VkFence *fences,
                         VkBool32 all, uint64_t timeout)
{
    uint64_t bound = timeout < 2000000000ULL ? timeout : 2000000000ULL;
    fprintf(stderr, "PSX_BEGIN wait_fences count=%u timeout_ns=%llu\n", count,
            (unsigned long long)bound); fflush(stderr);
    VkResult result = vkWaitForFences(device, count, fences, all, bound);
    fprintf(stderr, "PSX_END wait_fences result=%d\n", result); fflush(stderr);
    if (result != VK_SUCCESS) {
        fputs("PSX_STOP fence wait failed; submitted GPU work is not cancelled\n", stderr);
        fflush(NULL); _Exit(result == VK_TIMEOUT ? 124 : 1);
    }
    return result;
}

static VkResult psx_acquire(VkDevice device, VkSwapchainKHR swapchain, uint64_t timeout,
                            VkSemaphore semaphore, VkFence fence, uint32_t *index)
{
    uint64_t bound = timeout < 2000000000ULL ? timeout : 2000000000ULL;
    fprintf(stderr, "PSX_BEGIN acquire timeout_ns=%llu\n", (unsigned long long)bound); fflush(stderr);
    VkResult result = vkAcquireNextImageKHR(device, swapchain, bound, semaphore, fence, index);
    fprintf(stderr, "PSX_END acquire result=%d\n", result); fflush(stderr);
    if (result == VK_TIMEOUT || result == VK_NOT_READY) {
        fputs("PSX_STOP acquire did not complete; no retry\n", stderr); fflush(NULL); _Exit(124);
    }
    return result;
}

static VkResult psx_present(VkQueue queue, const VkPresentInfoKHR *info)
{
    fprintf(stderr, "PSX_BEGIN present waits=%u images=%u\n", info->waitSemaphoreCount,
            info->swapchainCount); fflush(stderr);
    VkResult result = vkQueuePresentKHR(queue, info);
    fprintf(stderr, "PSX_END present result=%d\n", result); fflush(stderr);
    return result;
}

static VkResult psx_idle(VkDevice device)
{
    fputs("PSX_BEGIN device_wait_idle\n", stderr); fflush(stderr);
    VkResult result = vkDeviceWaitIdle(device);
    fprintf(stderr, "PSX_END device_wait_idle result=%d\n", result); fflush(stderr);
    return result;
}

#define vkCreateDevice psx_create_device
#define vkCreateSwapchainKHR psx_swapchain
#define vkQueueSubmit psx_submit
#define vkWaitForFences psx_wait
#define vkAcquireNextImageKHR psx_acquire
#define vkQueuePresentKHR psx_present
#define vkDeviceWaitIdle psx_idle
