// SPDX-License-Identifier: MIT
/* Bounded Liverpool Vulkan diagnosis. No GPU access without --run MODE.
 * A timeout does not cancel GPU work: stop the hardware experiment on failure.
 */
#define _POSIX_C_SOURCE 200809L
#include <vulkan/vulkan.h>
#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
#include <string.h>
#include "vulkan-probe-shaders.h"

enum { WIDTH = 64, HEIGHT = 64, MAX_ALLOCATION = 16 * 1024 * 1024 };

static void check(VkResult result, const char *stage)
{
    printf("STAGE %s result=%d\n", stage, result);
    if (result != VK_SUCCESS) {
        fprintf(stderr, "STOP %s result=%d; no retry or GPU reset\n", stage, result);
        fflush(NULL);
        /* Do not enter Vulkan cleanup that might wait on an uncompleted job. */
        _Exit(result == VK_TIMEOUT ? 124 : 1);
    }
}

static uint32_t memory_type(const VkPhysicalDeviceMemoryProperties *memory,
                            uint32_t bits, VkMemoryPropertyFlags required)
{
    for (uint32_t i = 0; i < memory->memoryTypeCount; ++i) {
        if ((bits & (1u << i)) &&
            (memory->memoryTypes[i].propertyFlags & required) == required)
            return i;
    }
    fprintf(stderr, "STOP no compatible memory type\n");
    exit(1);
}

static VkDeviceMemory allocate(VkDevice device, VkMemoryRequirements requirements,
                                uint32_t type)
{
    if (!requirements.size || requirements.size > MAX_ALLOCATION) {
        fprintf(stderr, "STOP unexpected memory allocation size\n");
        exit(1);
    }
    VkMemoryAllocateInfo info = {
        .sType = VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO,
        .allocationSize = requirements.size,
        .memoryTypeIndex = type,
    };
    VkDeviceMemory memory;
    check(vkAllocateMemory(device, &info, NULL, &memory), "allocate_memory");
    return memory;
}

static void driver_maps(void)
{
    FILE *file = fopen("/proc/self/maps", "r");
    char line[2048];
    if (!file) {
        perror("/proc/self/maps");
        exit(1);
    }
    while (fgets(line, sizeof(line), file)) {
        if (strstr(line, "libvulkan"))
            printf("MAP %s", line);
    }
    fclose(file);
}

static VkPipeline draw_pipeline(VkDevice device, VkRenderPass render_pass,
                                VkPipelineLayout layout, int depth)
{
    VkShaderModuleCreateInfo shader_info = {
        .sType = VK_STRUCTURE_TYPE_SHADER_MODULE_CREATE_INFO,
        .codeSize = sizeof(vertex_spirv), .pCode = vertex_spirv,
    };
    VkShaderModule vertex, fragment;
    check(vkCreateShaderModule(device, &shader_info, NULL, &vertex), "vertex_module");
    shader_info.codeSize = sizeof(fragment_spirv);
    shader_info.pCode = fragment_spirv;
    check(vkCreateShaderModule(device, &shader_info, NULL, &fragment), "fragment_module");
    VkPipelineShaderStageCreateInfo shaders[] = {
        {.sType = VK_STRUCTURE_TYPE_PIPELINE_SHADER_STAGE_CREATE_INFO,
         .stage = VK_SHADER_STAGE_VERTEX_BIT, .module = vertex, .pName = "main"},
        {.sType = VK_STRUCTURE_TYPE_PIPELINE_SHADER_STAGE_CREATE_INFO,
         .stage = VK_SHADER_STAGE_FRAGMENT_BIT, .module = fragment, .pName = "main"},
    };
    VkPipelineVertexInputStateCreateInfo input = {
        .sType = VK_STRUCTURE_TYPE_PIPELINE_VERTEX_INPUT_STATE_CREATE_INFO,
    };
    VkPipelineInputAssemblyStateCreateInfo assembly = {
        .sType = VK_STRUCTURE_TYPE_PIPELINE_INPUT_ASSEMBLY_STATE_CREATE_INFO,
        .topology = VK_PRIMITIVE_TOPOLOGY_TRIANGLE_LIST,
    };
    VkViewport viewport = {0, 0, WIDTH, HEIGHT, 0, 1};
    VkRect2D scissor = {{0, 0}, {WIDTH, HEIGHT}};
    VkPipelineViewportStateCreateInfo viewports = {
        .sType = VK_STRUCTURE_TYPE_PIPELINE_VIEWPORT_STATE_CREATE_INFO,
        .viewportCount = 1, .pViewports = &viewport, .scissorCount = 1, .pScissors = &scissor,
    };
    VkPipelineRasterizationStateCreateInfo raster = {
        .sType = VK_STRUCTURE_TYPE_PIPELINE_RASTERIZATION_STATE_CREATE_INFO,
        .polygonMode = VK_POLYGON_MODE_FILL, .cullMode = VK_CULL_MODE_NONE,
        .frontFace = VK_FRONT_FACE_COUNTER_CLOCKWISE, .lineWidth = 1,
    };
    VkPipelineMultisampleStateCreateInfo samples = {
        .sType = VK_STRUCTURE_TYPE_PIPELINE_MULTISAMPLE_STATE_CREATE_INFO,
        .rasterizationSamples = VK_SAMPLE_COUNT_1_BIT,
    };
    VkPipelineColorBlendAttachmentState attachment = {.colorWriteMask = 0xf};
    VkPipelineColorBlendStateCreateInfo blend = {
        .sType = VK_STRUCTURE_TYPE_PIPELINE_COLOR_BLEND_STATE_CREATE_INFO,
        .attachmentCount = 1, .pAttachments = &attachment,
    };
    VkPipelineDepthStencilStateCreateInfo depth_state = {
        .sType = VK_STRUCTURE_TYPE_PIPELINE_DEPTH_STENCIL_STATE_CREATE_INFO,
        .depthTestEnable = VK_TRUE, .depthWriteEnable = VK_TRUE,
        .depthCompareOp = VK_COMPARE_OP_LESS_OR_EQUAL,
    };
    VkGraphicsPipelineCreateInfo info = {
        .sType = VK_STRUCTURE_TYPE_GRAPHICS_PIPELINE_CREATE_INFO,
        .stageCount = 2, .pStages = shaders, .pVertexInputState = &input,
        .pInputAssemblyState = &assembly, .pViewportState = &viewports,
        .pRasterizationState = &raster, .pMultisampleState = &samples,
        .pColorBlendState = &blend, .layout = layout, .renderPass = render_pass,
        .pDepthStencilState = depth ? &depth_state : NULL,
    };
    VkPipeline pipeline;
    check(vkCreateGraphicsPipelines(device, VK_NULL_HANDLE, 1, &info, NULL, &pipeline),
          "create_graphics_pipeline");
    vkDestroyShaderModule(device, fragment, NULL);
    vkDestroyShaderModule(device, vertex, NULL);
    return pipeline;
}

static int fill_probe(VkDevice device, VkPhysicalDevice physical, uint32_t family,
                      const char *mode, int compile_only)
{
    enum { CAPACITY = 16384 };
    const uint32_t bytes = !strcmp(mode, "fill-cp") ? 4092 : 4096;
    const uint32_t pattern = 0x1234abcd;
    VkPhysicalDeviceMemoryProperties memory;
    vkGetPhysicalDeviceMemoryProperties(physical, &memory);
    VkBufferCreateInfo buffer_info = {
        .sType = VK_STRUCTURE_TYPE_BUFFER_CREATE_INFO,
        .size = CAPACITY, .usage = VK_BUFFER_USAGE_TRANSFER_DST_BIT,
        .sharingMode = VK_SHARING_MODE_EXCLUSIVE,
    };
    VkBuffer buffer;
    check(vkCreateBuffer(device, &buffer_info, NULL, &buffer), "fill_create_buffer");
    VkMemoryRequirements requirements;
    vkGetBufferMemoryRequirements(device, buffer, &requirements);
    uint32_t type = memory_type(&memory, requirements.memoryTypeBits,
        VK_MEMORY_PROPERTY_DEVICE_LOCAL_BIT | VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT);
    printf("FILL bytes=%u capacity=%u memory_type=%u flags=%x heap=%u compile_only=%d\n",
           bytes, CAPACITY, type, memory.memoryTypes[type].propertyFlags,
           memory.memoryTypes[type].heapIndex, compile_only);
    VkDeviceMemory allocation = allocate(device, requirements, type);
    check(vkBindBufferMemory(device, buffer, allocation, 0), "fill_bind_buffer");
    void *mapped = NULL;
    VkMappedMemoryRange mapped_range = {
        .sType = VK_STRUCTURE_TYPE_MAPPED_MEMORY_RANGE,
        .memory = allocation, .offset = 0, .size = VK_WHOLE_SIZE,
    };
    if (!compile_only) {
        check(vkMapMemory(device, allocation, 0, VK_WHOLE_SIZE, 0, &mapped), "fill_map");
        memset(mapped, 0xcd, CAPACITY);
        check(vkFlushMappedMemoryRanges(device, 1, &mapped_range), "fill_flush_sentinel");
    }
    VkCommandPoolCreateInfo pool_info = {
        .sType = VK_STRUCTURE_TYPE_COMMAND_POOL_CREATE_INFO, .queueFamilyIndex = family,
    };
    VkCommandPool pool;
    check(vkCreateCommandPool(device, &pool_info, NULL, &pool), "fill_command_pool");
    VkCommandBufferAllocateInfo command_info = {
        .sType = VK_STRUCTURE_TYPE_COMMAND_BUFFER_ALLOCATE_INFO,
        .commandPool = pool, .level = VK_COMMAND_BUFFER_LEVEL_PRIMARY, .commandBufferCount = 1,
    };
    VkCommandBuffer command;
    check(vkAllocateCommandBuffers(device, &command_info, &command), "fill_allocate_command");
    VkCommandBufferBeginInfo begin = {
        .sType = VK_STRUCTURE_TYPE_COMMAND_BUFFER_BEGIN_INFO,
        .flags = VK_COMMAND_BUFFER_USAGE_ONE_TIME_SUBMIT_BIT,
    };
    check(vkBeginCommandBuffer(command, &begin), "fill_begin");
    VkBufferMemoryBarrier barrier = {
        .sType = VK_STRUCTURE_TYPE_BUFFER_MEMORY_BARRIER,
        .srcAccessMask = VK_ACCESS_HOST_WRITE_BIT,
        .dstAccessMask = VK_ACCESS_TRANSFER_WRITE_BIT,
        .srcQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED,
        .dstQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED,
        .buffer = buffer, .offset = 0, .size = CAPACITY,
    };
    vkCmdPipelineBarrier(command, VK_PIPELINE_STAGE_HOST_BIT, VK_PIPELINE_STAGE_TRANSFER_BIT,
                         0, 0, NULL, 1, &barrier, 0, NULL);
    vkCmdFillBuffer(command, buffer, 0, bytes, pattern);
    barrier.srcAccessMask = VK_ACCESS_TRANSFER_WRITE_BIT;
    barrier.dstAccessMask = VK_ACCESS_HOST_READ_BIT;
    vkCmdPipelineBarrier(command, VK_PIPELINE_STAGE_TRANSFER_BIT, VK_PIPELINE_STAGE_HOST_BIT,
                         0, 0, NULL, 1, &barrier, 0, NULL);
    check(vkEndCommandBuffer(command), "fill_end");
    VkFence fence = VK_NULL_HANDLE;
    int failed = 0;
    if (compile_only) {
        printf("RESULT COMPILE_ONLY mode=%s submitted=0 bytes=%u; synthetic profile, not hardware proof\n",
               mode, bytes);
    } else {
        VkFenceCreateInfo fence_info = {.sType = VK_STRUCTURE_TYPE_FENCE_CREATE_INFO};
        check(vkCreateFence(device, &fence_info, NULL, &fence), "fill_create_fence");
        VkQueue queue;
        vkGetDeviceQueue(device, family, 0, &queue);
        VkSubmitInfo submit = {
            .sType = VK_STRUCTURE_TYPE_SUBMIT_INFO,
            .commandBufferCount = 1, .pCommandBuffers = &command,
        };
        check(vkQueueSubmit(queue, 1, &submit, fence), "fill_submit_once");
        check(vkWaitForFences(device, 1, &fence, VK_TRUE, 2000000000ull), "fill_wait_fence_2s");
        check(vkInvalidateMappedMemoryRanges(device, 1, &mapped_range), "fill_invalidate");
        const uint32_t *words = mapped;
        for (uint32_t i = 0; i < CAPACITY / 4; ++i) {
            const uint32_t expected = i < bytes / 4 ? pattern : 0xcdcdcdcd;
            if (words[i] != expected) {
                fprintf(stderr, "STOP fill mismatch word=%u actual=%08x expected=%08x\n",
                        i, words[i], expected);
                failed = 1;
                break;
            }
        }
        if (!failed)
            printf("RESULT PASS mode=%s submitted=1 filled_bytes=%u checked_bytes=%u guard_bytes=%u\n",
                   mode, bytes, CAPACITY, CAPACITY - bytes);
    }
    if (fence) vkDestroyFence(device, fence, NULL);
    vkDestroyCommandPool(device, pool, NULL);
    if (mapped) vkUnmapMemory(device, allocation);
    vkDestroyBuffer(device, buffer, NULL);
    vkFreeMemory(device, allocation, NULL);
    return failed;
}

int main(int argc, char **argv)
{
    setvbuf(stdout, NULL, _IOLBF, 0);
    const int compile_only = argc == 3 && !strcmp(argv[1], "--compile-only");
    if (argc != 3 || (strcmp(argv[1], "--run") && !compile_only) ||
        (strcmp(argv[2], "device") && strcmp(argv[2], "clear") && strcmp(argv[2], "draw") &&
         strcmp(argv[2], "depth") && strcmp(argv[2], "fill-cp") && strcmp(argv[2], "fill-cs")) ||
        (compile_only && strcmp(argv[2], "fill-cp") && strcmp(argv[2], "fill-cs"))) {
        puts("Usage: vulkan-probe --run device|clear|draw|depth|fill-cp|fill-cs");
        puts("       vulkan-probe --compile-only fill-cp|fill-cs (isolated DRM shim only)");
        puts("device: create/destroy one Liverpool graphics device; no submitted commands.");
        puts("clear: one 64x64 optimal image clear/copy; 2s fence bound and exact pixel oracle.");
        puts("draw: one fullscreen triangle; same readback oracle, expected solid green.");
        puts("depth: same triangle with D16 optimal depth clear/test/write; green oracle.");
        puts("fill-cp/fill-cs: 4092/4096-byte fill in the same 16KiB host-visible VRAM allocation.");
        puts("No GPU access without explicit --run. Timeout does not cancel GPU work.");
        return argc == 2 && !strcmp(argv[1], "--help") ? 0 : 2;
    }
    if (compile_only && (!getenv("AMDGPU_GPU_ID") ||
                        strcmp(getenv("AMDGPU_GPU_ID"), "bonaire") ||
                        !getenv("LD_PRELOAD") ||
                        !strstr(getenv("LD_PRELOAD"), "libamdgpu_noop_drm_shim.so"))) {
        fprintf(stderr, "STOP compile-only requires the explicit Bonaire DRM shim in a device-hidden sandbox\n");
        return 2;
    }
    printf("CONFIG mode=%s RADV_DEBUG=%s\n", argv[2],
           getenv("RADV_DEBUG") ? getenv("RADV_DEBUG") : "");
    VkApplicationInfo app = {
        .sType = VK_STRUCTURE_TYPE_APPLICATION_INFO,
        .pApplicationName = "psx-vulkan-probe",
        .apiVersion = VK_API_VERSION_1_0,
    };
    VkInstanceCreateInfo instance_info = {
        .sType = VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO,
        .pApplicationInfo = &app,
    };
    VkInstance instance;
    check(vkCreateInstance(&instance_info, NULL, &instance), "create_instance");
    uint32_t count = 0;
    check(vkEnumeratePhysicalDevices(instance, &count, NULL), "count_devices");
    if (!count || count > 16) return 1;
    VkPhysicalDevice devices[16], physical = VK_NULL_HANDLE;
    check(vkEnumeratePhysicalDevices(instance, &count, devices), "enumerate_devices");
    for (uint32_t i = 0; i < count; ++i) {
        VkPhysicalDeviceProperties props;
        vkGetPhysicalDeviceProperties(devices[i], &props);
        printf("DEVICE vendor=%04x device=%04x api=%u driver=%u name=%s\n",
               props.vendorID, props.deviceID, props.apiVersion, props.driverVersion,
               props.deviceName);
        if (props.vendorID == 0x1002 &&
            ((!compile_only && props.deviceID == 0x9923) ||
             (compile_only && strstr(props.deviceName, "BONAIRE"))))
            physical = devices[i];
    }
    if (!physical) {
        fprintf(stderr, "STOP Liverpool device not found; no software fallback\n");
        return 1;
    }
    uint32_t queue_count = 0, family = UINT32_MAX;
    vkGetPhysicalDeviceQueueFamilyProperties(physical, &queue_count, NULL);
    if (!queue_count || queue_count > 32) return 1;
    VkQueueFamilyProperties families[32];
    vkGetPhysicalDeviceQueueFamilyProperties(physical, &queue_count, families);
    for (uint32_t i = 0; i < queue_count; ++i) {
        printf("QUEUE family=%u flags=%x count=%u\n", i, families[i].queueFlags,
               families[i].queueCount);
        if (family == UINT32_MAX && families[i].queueCount &&
            (families[i].queueFlags & VK_QUEUE_GRAPHICS_BIT)) family = i;
    }
    if (family == UINT32_MAX) return 1;
    const float priority = 1.0f;
    VkDeviceQueueCreateInfo queue_info = {
        .sType = VK_STRUCTURE_TYPE_DEVICE_QUEUE_CREATE_INFO,
        .queueFamilyIndex = family, .queueCount = 1, .pQueuePriorities = &priority,
    };
    VkDeviceCreateInfo device_info = {
        .sType = VK_STRUCTURE_TYPE_DEVICE_CREATE_INFO,
        .queueCreateInfoCount = 1, .pQueueCreateInfos = &queue_info,
    };
    VkDevice device;
    check(vkCreateDevice(physical, &device_info, NULL, &device), "create_device");
    driver_maps();
    if (!strcmp(argv[2], "fill-cp") || !strcmp(argv[2], "fill-cs")) {
        int result = fill_probe(device, physical, family, argv[2], compile_only);
        vkDestroyDevice(device, NULL);
        vkDestroyInstance(instance, NULL);
        return result;
    }
    if (!strcmp(argv[2], "device")) {
        vkDestroyDevice(device, NULL);
        vkDestroyInstance(instance, NULL);
        puts("RESULT PASS mode=device submitted=0");
        return 0;
    }
    const int depth = !strcmp(argv[2], "depth");
    const int draw = depth || !strcmp(argv[2], "draw");
    VkQueue queue;
    vkGetDeviceQueue(device, family, 0, &queue);
    VkPhysicalDeviceMemoryProperties memory;
    vkGetPhysicalDeviceMemoryProperties(physical, &memory);
    VkImageCreateInfo image_info = {
        .sType = VK_STRUCTURE_TYPE_IMAGE_CREATE_INFO,
        .imageType = VK_IMAGE_TYPE_2D, .format = VK_FORMAT_R8G8B8A8_UNORM,
        .extent = {WIDTH, HEIGHT, 1}, .mipLevels = 1, .arrayLayers = 1,
        .samples = VK_SAMPLE_COUNT_1_BIT, .tiling = VK_IMAGE_TILING_OPTIMAL,
        .usage = VK_IMAGE_USAGE_TRANSFER_DST_BIT | VK_IMAGE_USAGE_TRANSFER_SRC_BIT |
                 VK_IMAGE_USAGE_COLOR_ATTACHMENT_BIT,
        .sharingMode = VK_SHARING_MODE_EXCLUSIVE, .initialLayout = VK_IMAGE_LAYOUT_UNDEFINED,
    };
    VkImage image;
    check(vkCreateImage(device, &image_info, NULL, &image), "create_image");
    VkMemoryRequirements requirements;
    vkGetImageMemoryRequirements(device, image, &requirements);
    VkDeviceMemory image_memory = allocate(device, requirements,
        memory_type(&memory, requirements.memoryTypeBits, VK_MEMORY_PROPERTY_DEVICE_LOCAL_BIT));
    check(vkBindImageMemory(device, image, image_memory, 0), "bind_image");
    VkRenderPass render_pass = VK_NULL_HANDLE;
    VkFramebuffer framebuffer = VK_NULL_HANDLE;
    VkImageView view = VK_NULL_HANDLE;
    VkPipelineLayout layout = VK_NULL_HANDLE;
    VkPipeline pipeline = VK_NULL_HANDLE;
    VkImage depth_image = VK_NULL_HANDLE;
    VkImageView depth_view = VK_NULL_HANDLE;
    VkDeviceMemory depth_memory = VK_NULL_HANDLE;
    if (depth) {
        VkFormatProperties properties;
        vkGetPhysicalDeviceFormatProperties(physical, VK_FORMAT_D16_UNORM, &properties);
        if (!(properties.optimalTilingFeatures & VK_FORMAT_FEATURE_DEPTH_STENCIL_ATTACHMENT_BIT)) {
            fprintf(stderr, "STOP D16 optimal depth unsupported\n"); return 1;
        }
        VkImageCreateInfo depth_info = image_info;
        depth_info.format = VK_FORMAT_D16_UNORM;
        depth_info.usage = VK_IMAGE_USAGE_DEPTH_STENCIL_ATTACHMENT_BIT;
        check(vkCreateImage(device, &depth_info, NULL, &depth_image), "create_depth_image");
        vkGetImageMemoryRequirements(device, depth_image, &requirements);
        depth_memory = allocate(device, requirements,
            memory_type(&memory, requirements.memoryTypeBits, VK_MEMORY_PROPERTY_DEVICE_LOCAL_BIT));
        check(vkBindImageMemory(device, depth_image, depth_memory, 0), "bind_depth_image");
        VkImageViewCreateInfo depth_view_info = {
            .sType = VK_STRUCTURE_TYPE_IMAGE_VIEW_CREATE_INFO, .image = depth_image,
            .viewType = VK_IMAGE_VIEW_TYPE_2D, .format = VK_FORMAT_D16_UNORM,
            .subresourceRange = {VK_IMAGE_ASPECT_DEPTH_BIT, 0, 1, 0, 1},
        };
        check(vkCreateImageView(device, &depth_view_info, NULL, &depth_view), "create_depth_view");
    }
    if (draw) {
        VkAttachmentDescription attachments[2] = {{
            .format = VK_FORMAT_R8G8B8A8_UNORM, .samples = VK_SAMPLE_COUNT_1_BIT,
            .loadOp = VK_ATTACHMENT_LOAD_OP_CLEAR, .storeOp = VK_ATTACHMENT_STORE_OP_STORE,
            .stencilLoadOp = VK_ATTACHMENT_LOAD_OP_DONT_CARE,
            .stencilStoreOp = VK_ATTACHMENT_STORE_OP_DONT_CARE,
            .initialLayout = VK_IMAGE_LAYOUT_COLOR_ATTACHMENT_OPTIMAL,
            .finalLayout = VK_IMAGE_LAYOUT_COLOR_ATTACHMENT_OPTIMAL,
        }, {
            .format = VK_FORMAT_D16_UNORM, .samples = VK_SAMPLE_COUNT_1_BIT,
            .loadOp = VK_ATTACHMENT_LOAD_OP_CLEAR, .storeOp = VK_ATTACHMENT_STORE_OP_STORE,
            .stencilLoadOp = VK_ATTACHMENT_LOAD_OP_DONT_CARE,
            .stencilStoreOp = VK_ATTACHMENT_STORE_OP_DONT_CARE,
            .initialLayout = VK_IMAGE_LAYOUT_DEPTH_STENCIL_ATTACHMENT_OPTIMAL,
            .finalLayout = VK_IMAGE_LAYOUT_DEPTH_STENCIL_ATTACHMENT_OPTIMAL,
        }};
        VkAttachmentReference reference = {0, VK_IMAGE_LAYOUT_COLOR_ATTACHMENT_OPTIMAL};
        VkAttachmentReference depth_reference = {1, VK_IMAGE_LAYOUT_DEPTH_STENCIL_ATTACHMENT_OPTIMAL};
        VkSubpassDescription subpass = {
            .pipelineBindPoint = VK_PIPELINE_BIND_POINT_GRAPHICS,
            .colorAttachmentCount = 1, .pColorAttachments = &reference,
            .pDepthStencilAttachment = depth ? &depth_reference : NULL,
        };
        VkRenderPassCreateInfo pass_info = {
            .sType = VK_STRUCTURE_TYPE_RENDER_PASS_CREATE_INFO,
            .attachmentCount = depth ? 2 : 1, .pAttachments = attachments,
            .subpassCount = 1, .pSubpasses = &subpass,
        };
        check(vkCreateRenderPass(device, &pass_info, NULL, &render_pass), "create_render_pass");
        VkImageViewCreateInfo view_info = {
            .sType = VK_STRUCTURE_TYPE_IMAGE_VIEW_CREATE_INFO, .image = image,
            .viewType = VK_IMAGE_VIEW_TYPE_2D, .format = VK_FORMAT_R8G8B8A8_UNORM,
            .subresourceRange = {VK_IMAGE_ASPECT_COLOR_BIT, 0, 1, 0, 1},
        };
        check(vkCreateImageView(device, &view_info, NULL, &view), "create_image_view");
        VkImageView views[] = {view, depth_view};
        VkFramebufferCreateInfo frame_info = {
            .sType = VK_STRUCTURE_TYPE_FRAMEBUFFER_CREATE_INFO, .renderPass = render_pass,
            .attachmentCount = depth ? 2 : 1, .pAttachments = views,
            .width = WIDTH, .height = HEIGHT, .layers = 1,
        };
        check(vkCreateFramebuffer(device, &frame_info, NULL, &framebuffer), "create_framebuffer");
        VkPushConstantRange constants = {
            .stageFlags = VK_SHADER_STAGE_VERTEX_BIT | VK_SHADER_STAGE_FRAGMENT_BIT,
            .size = 5 * sizeof(float),
        };
        VkPipelineLayoutCreateInfo layout_info = {
            .sType = VK_STRUCTURE_TYPE_PIPELINE_LAYOUT_CREATE_INFO,
            .pushConstantRangeCount = 1, .pPushConstantRanges = &constants,
        };
        check(vkCreatePipelineLayout(device, &layout_info, NULL, &layout), "create_pipeline_layout");
        pipeline = draw_pipeline(device, render_pass, layout, depth);
    }
    VkBufferCreateInfo buffer_info = {
        .sType = VK_STRUCTURE_TYPE_BUFFER_CREATE_INFO,
        .size = WIDTH * HEIGHT * 4, .usage = VK_BUFFER_USAGE_TRANSFER_DST_BIT,
        .sharingMode = VK_SHARING_MODE_EXCLUSIVE,
    };
    VkBuffer buffer;
    check(vkCreateBuffer(device, &buffer_info, NULL, &buffer), "create_buffer");
    vkGetBufferMemoryRequirements(device, buffer, &requirements);
    VkDeviceMemory buffer_memory = allocate(device, requirements,
        memory_type(&memory, requirements.memoryTypeBits, VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT));
    check(vkBindBufferMemory(device, buffer, buffer_memory, 0), "bind_buffer");
    VkCommandPoolCreateInfo pool_info = {
        .sType = VK_STRUCTURE_TYPE_COMMAND_POOL_CREATE_INFO, .queueFamilyIndex = family,
    };
    VkCommandPool pool;
    check(vkCreateCommandPool(device, &pool_info, NULL, &pool), "create_command_pool");
    VkCommandBufferAllocateInfo command_info = {
        .sType = VK_STRUCTURE_TYPE_COMMAND_BUFFER_ALLOCATE_INFO,
        .commandPool = pool, .level = VK_COMMAND_BUFFER_LEVEL_PRIMARY, .commandBufferCount = 1,
    };
    VkCommandBuffer command;
    check(vkAllocateCommandBuffers(device, &command_info, &command), "allocate_command_buffer");
    VkCommandBufferBeginInfo begin = {
        .sType = VK_STRUCTURE_TYPE_COMMAND_BUFFER_BEGIN_INFO,
        .flags = VK_COMMAND_BUFFER_USAGE_ONE_TIME_SUBMIT_BIT,
    };
    check(vkBeginCommandBuffer(command, &begin), "begin_command_buffer");
    if (depth) {
        VkImageMemoryBarrier barrier = {
            .sType = VK_STRUCTURE_TYPE_IMAGE_MEMORY_BARRIER,
            .dstAccessMask = VK_ACCESS_DEPTH_STENCIL_ATTACHMENT_READ_BIT |
                             VK_ACCESS_DEPTH_STENCIL_ATTACHMENT_WRITE_BIT,
            .oldLayout = VK_IMAGE_LAYOUT_UNDEFINED,
            .newLayout = VK_IMAGE_LAYOUT_DEPTH_STENCIL_ATTACHMENT_OPTIMAL,
            .srcQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED,
            .dstQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED,
            .image = depth_image, .subresourceRange = {VK_IMAGE_ASPECT_DEPTH_BIT, 0, 1, 0, 1},
        };
        vkCmdPipelineBarrier(command, VK_PIPELINE_STAGE_TOP_OF_PIPE_BIT,
                             VK_PIPELINE_STAGE_EARLY_FRAGMENT_TESTS_BIT |
                             VK_PIPELINE_STAGE_LATE_FRAGMENT_TESTS_BIT,
                             0, 0, NULL, 0, NULL, 1, &barrier);
    }
    VkImageMemoryBarrier image_barrier = {
        .sType = VK_STRUCTURE_TYPE_IMAGE_MEMORY_BARRIER,
        .dstAccessMask = draw ? VK_ACCESS_COLOR_ATTACHMENT_WRITE_BIT : VK_ACCESS_TRANSFER_WRITE_BIT,
        .oldLayout = VK_IMAGE_LAYOUT_UNDEFINED,
        .newLayout = draw ? VK_IMAGE_LAYOUT_COLOR_ATTACHMENT_OPTIMAL : VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL,
        .srcQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED, .dstQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED,
        .image = image, .subresourceRange = {VK_IMAGE_ASPECT_COLOR_BIT, 0, 1, 0, 1},
    };
    VkPipelineStageFlags write_stage = draw ? VK_PIPELINE_STAGE_COLOR_ATTACHMENT_OUTPUT_BIT :
                                              VK_PIPELINE_STAGE_TRANSFER_BIT;
    vkCmdPipelineBarrier(command, VK_PIPELINE_STAGE_TOP_OF_PIPE_BIT, write_stage,
                         0, 0, NULL, 0, NULL, 1, &image_barrier);
    if (draw) {
        VkClearValue clears[] = {{.color = {.float32 = {0, 0, 0, 1}}},
                                 {.depthStencil = {1.0f, 0}}};
        VkRenderPassBeginInfo render = {
            .sType = VK_STRUCTURE_TYPE_RENDER_PASS_BEGIN_INFO, .renderPass = render_pass,
            .framebuffer = framebuffer, .renderArea = {{0, 0}, {WIDTH, HEIGHT}},
            .clearValueCount = depth ? 2 : 1, .pClearValues = clears,
        };
        vkCmdBeginRenderPass(command, &render, VK_SUBPASS_CONTENTS_INLINE);
        vkCmdBindPipeline(command, VK_PIPELINE_BIND_POINT_GRAPHICS, pipeline);
        float parameters[] = {0, 1, 0, 1, 0.25f};
        vkCmdPushConstants(command, layout, VK_SHADER_STAGE_VERTEX_BIT | VK_SHADER_STAGE_FRAGMENT_BIT,
                           0, sizeof(parameters), parameters);
        vkCmdDraw(command, 3, 1, 0, 0);
        if (depth) {
            /* Far red must be rejected behind the near green draw. */
            parameters[0] = 1; parameters[1] = 0; parameters[4] = 0.75f;
            vkCmdPushConstants(command, layout, VK_SHADER_STAGE_VERTEX_BIT | VK_SHADER_STAGE_FRAGMENT_BIT,
                               0, sizeof(parameters), parameters);
            vkCmdDraw(command, 3, 1, 0, 0);
        }
        vkCmdEndRenderPass(command);
    } else {
        VkClearColorValue color = {.float32 = {1.0f, 0.0f, 1.0f, 1.0f}};
        vkCmdClearColorImage(command, image, VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL,
                             &color, 1, &image_barrier.subresourceRange);
    }
    image_barrier.srcAccessMask = draw ? VK_ACCESS_COLOR_ATTACHMENT_WRITE_BIT : VK_ACCESS_TRANSFER_WRITE_BIT;
    image_barrier.dstAccessMask = VK_ACCESS_TRANSFER_READ_BIT;
    image_barrier.oldLayout = image_barrier.newLayout;
    image_barrier.newLayout = VK_IMAGE_LAYOUT_TRANSFER_SRC_OPTIMAL;
    vkCmdPipelineBarrier(command, write_stage, VK_PIPELINE_STAGE_TRANSFER_BIT,
                         0, 0, NULL, 0, NULL, 1, &image_barrier);
    VkBufferImageCopy copy = {
        .imageSubresource = {VK_IMAGE_ASPECT_COLOR_BIT, 0, 0, 1},
        .imageExtent = {WIDTH, HEIGHT, 1},
    };
    vkCmdCopyImageToBuffer(command, image, VK_IMAGE_LAYOUT_TRANSFER_SRC_OPTIMAL, buffer, 1, &copy);
    VkBufferMemoryBarrier host_barrier = {
        .sType = VK_STRUCTURE_TYPE_BUFFER_MEMORY_BARRIER,
        .srcAccessMask = VK_ACCESS_TRANSFER_WRITE_BIT, .dstAccessMask = VK_ACCESS_HOST_READ_BIT,
        .srcQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED, .dstQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED,
        .buffer = buffer, .size = VK_WHOLE_SIZE,
    };
    vkCmdPipelineBarrier(command, VK_PIPELINE_STAGE_TRANSFER_BIT, VK_PIPELINE_STAGE_HOST_BIT,
                         0, 0, NULL, 1, &host_barrier, 0, NULL);
    check(vkEndCommandBuffer(command), "end_command_buffer");
    VkFenceCreateInfo fence_info = {.sType = VK_STRUCTURE_TYPE_FENCE_CREATE_INFO};
    VkFence fence;
    check(vkCreateFence(device, &fence_info, NULL, &fence), "create_fence");
    VkSubmitInfo submit = {
        .sType = VK_STRUCTURE_TYPE_SUBMIT_INFO, .commandBufferCount = 1,
        .pCommandBuffers = &command,
    };
    check(vkQueueSubmit(queue, 1, &submit, fence), "submit_once");
    check(vkWaitForFences(device, 1, &fence, VK_TRUE, 2000000000ULL), "wait_fence_2s");
    void *mapped;
    check(vkMapMemory(device, buffer_memory, 0, VK_WHOLE_SIZE, 0, &mapped), "map_readback");
    VkMappedMemoryRange range = {
        .sType = VK_STRUCTURE_TYPE_MAPPED_MEMORY_RANGE,
        .memory = buffer_memory, .size = VK_WHOLE_SIZE,
    };
    check(vkInvalidateMappedMemoryRanges(device, 1, &range), "invalidate_readback");
    const uint8_t *pixels = mapped;
    for (unsigned i = 0; i < WIDTH * HEIGHT; ++i) {
        const uint8_t expected[] = {draw ? 0 : 255, draw ? 255 : 0, draw ? 0 : 255, 255};
        if (memcmp(pixels + i * 4, expected, 4)) {
            fprintf(stderr, "STOP pixel=%u actual=%02x%02x%02x%02x\n", i,
                    pixels[i*4], pixels[i*4+1], pixels[i*4+2], pixels[i*4+3]);
            return 1;
        }
    }
    vkUnmapMemory(device, buffer_memory);
    vkDestroyFence(device, fence, NULL);
    vkDestroyCommandPool(device, pool, NULL);
    if (draw) {
        vkDestroyPipeline(device, pipeline, NULL);
        vkDestroyPipelineLayout(device, layout, NULL);
        vkDestroyFramebuffer(device, framebuffer, NULL);
        vkDestroyImageView(device, view, NULL);
        vkDestroyRenderPass(device, render_pass, NULL);
    }
    if (depth) {
        vkDestroyImageView(device, depth_view, NULL);
        vkDestroyImage(device, depth_image, NULL);
        vkFreeMemory(device, depth_memory, NULL);
    }
    vkDestroyBuffer(device, buffer, NULL);
    vkFreeMemory(device, buffer_memory, NULL);
    vkDestroyImage(device, image, NULL);
    vkFreeMemory(device, image_memory, NULL);
    vkDestroyDevice(device, NULL);
    vkDestroyInstance(instance, NULL);
    printf("RESULT PASS mode=%s submitted=1 checked_pixels=%u rgba=%s\n",
           argv[2], WIDTH * HEIGHT, draw ? "00ff00ff" : "ff00ffff");
    return 0;
}
