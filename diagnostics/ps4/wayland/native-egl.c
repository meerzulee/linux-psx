/* SPDX-License-Identifier: MIT
 * Native Wayland/EGL ES2 diagnostic. No display is opened for --help.
 * A completed frame is evidence only; it does not qualify this hardware.
 */
#define _POSIX_C_SOURCE 200809L
#include <EGL/egl.h>
#include <GLES2/gl2.h>
#include <wayland-client.h>
#include <wayland-egl.h>
#include "xdg-shell-client-protocol.h"
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>
#include <unistd.h>

struct app {
    struct wl_display *display;
    struct wl_compositor *compositor;
    struct xdg_wm_base *wm;
    struct wl_surface *surface;
    struct xdg_surface *xdg_surface;
    struct xdg_toplevel *toplevel;
    struct wl_egl_window *window;
    bool configured, closed;
    int width, height;
};

static void json_string(const char *value) {
    putchar('"');
    for (const unsigned char *p = (const unsigned char *)(value ? value : ""); *p; p++) {
        if (*p == '"' || *p == '\\') printf("\\%c", *p);
        else if (*p < 32) printf("\\u%04x", *p);
        else putchar(*p);
    }
    putchar('"');
}

static void ping(void *data, struct xdg_wm_base *wm, uint32_t serial) {
    (void)data;
    xdg_wm_base_pong(wm, serial);
}
static const struct xdg_wm_base_listener wm_listener = {.ping = ping};

static void registry_global(void *data, struct wl_registry *registry, uint32_t name,
                            const char *interface, uint32_t version) {
    struct app *a = data;
    if (!strcmp(interface, "wl_compositor"))
        a->compositor = wl_registry_bind(registry, name, &wl_compositor_interface, version < 4 ? version : 4);
    else if (!strcmp(interface, "xdg_wm_base")) {
        a->wm = wl_registry_bind(registry, name, &xdg_wm_base_interface, version < 2 ? version : 2);
        xdg_wm_base_add_listener(a->wm, &wm_listener, a);
    }
}
static void registry_remove(void *data, struct wl_registry *registry, uint32_t name) {
    (void)data; (void)registry; (void)name;
}
static const struct wl_registry_listener registry_listener = {
    .global = registry_global, .global_remove = registry_remove
};

static void surface_configure(void *data, struct xdg_surface *surface, uint32_t serial) {
    struct app *a = data;
    xdg_surface_ack_configure(surface, serial);
    a->configured = true;
}
static const struct xdg_surface_listener surface_listener = {.configure = surface_configure};

static void toplevel_configure(void *data, struct xdg_toplevel *toplevel,
                              int32_t width, int32_t height, struct wl_array *states) {
    (void)toplevel; (void)states;
    struct app *a = data;
    if (width > 0 && width <= 4096) a->width = width;
    if (height > 0 && height <= 4096) a->height = height;
    if (a->window) wl_egl_window_resize(a->window, a->width, a->height, 0, 0);
}
static void toplevel_close(void *data, struct xdg_toplevel *toplevel) {
    (void)toplevel;
    ((struct app *)data)->closed = true;
}
static const struct xdg_toplevel_listener toplevel_listener = {
    .configure = toplevel_configure, .close = toplevel_close
};

static GLuint shader(GLenum kind, const char *text) {
    GLuint object = glCreateShader(kind);
    glShaderSource(object, 1, &text, NULL);
    glCompileShader(object);
    GLint ok = GL_FALSE;
    glGetShaderiv(object, GL_COMPILE_STATUS, &ok);
    if (ok != GL_TRUE) { glDeleteShader(object); return 0; }
    return object;
}

static bool near_pixel(const unsigned char *actual, const unsigned char *expected) {
    for (int n = 0; n < 4; n++)
        if (abs((int)actual[n] - (int)expected[n]) > 2) return false;
    return true;
}

static bool software_renderer(const char *name) {
    if (!name || !*name) return true;
    const char *tokens[] = {"llvmpipe", "softpipe", "software", "swrast", "lavapipe"};
    for (size_t n = 0; n < sizeof(tokens)/sizeof(tokens[0]); n++)
        for (const char *p = name; *p; p++)
            if (!strncasecmp(p, tokens[n], strlen(tokens[n]))) return true;
    return false;
}

int main(int argc, char **argv) {
    if (argc == 2 && !strcmp(argv[1], "--help")) {
        puts("psx-wayland-egl --run: native xdg-shell/EGL ES2 known-pixel test (15-second process bound)");
        return 0;
    }
    if (argc != 2 || strcmp(argv[1], "--run")) {
        fputs("Explicit --run is required.\n", stderr); return 2;
    }
    alarm(15);
    struct app a = {.width = 640, .height = 480};
    EGLDisplay display = EGL_NO_DISPLAY;
    EGLContext context = EGL_NO_CONTEXT;
    EGLSurface surface = EGL_NO_SURFACE;
    const char *phase = "wayland-connect";
    GLuint program = 0, vertex = 0, fragment = 0;
    int status = 1;
    a.display = wl_display_connect(NULL);
    if (!a.display) goto cleanup;
    struct wl_registry *registry = wl_display_get_registry(a.display);
    wl_registry_add_listener(registry, &registry_listener, &a);
    if (wl_display_roundtrip(a.display) < 0 || !a.compositor || !a.wm) goto cleanup;
    a.surface = wl_compositor_create_surface(a.compositor);
    a.xdg_surface = xdg_wm_base_get_xdg_surface(a.wm, a.surface);
    xdg_surface_add_listener(a.xdg_surface, &surface_listener, &a);
    a.toplevel = xdg_surface_get_toplevel(a.xdg_surface);
    xdg_toplevel_add_listener(a.toplevel, &toplevel_listener, &a);
    xdg_toplevel_set_title(a.toplevel, "psX native Wayland EGL test");
    xdg_toplevel_set_app_id(a.toplevel, "org.psx.diagnostics.wayland-egl");
    xdg_toplevel_set_fullscreen(a.toplevel, NULL);
    wl_surface_commit(a.surface);
    phase = "xdg-configure";
    while (!a.configured && !a.closed)
        if (wl_display_dispatch(a.display) < 0) goto cleanup;
    if (a.closed) goto cleanup;
    a.window = wl_egl_window_create(a.surface, a.width, a.height);
    if (!a.window) goto cleanup;
    phase = "egl-context";
    display = eglGetDisplay((EGLNativeDisplayType)a.display);
    if (display == EGL_NO_DISPLAY || !eglInitialize(display, NULL, NULL) || !eglBindAPI(EGL_OPENGL_ES_API)) goto cleanup;
    EGLint attributes[] = {EGL_SURFACE_TYPE, EGL_WINDOW_BIT, EGL_RENDERABLE_TYPE, EGL_OPENGL_ES2_BIT,
        EGL_RED_SIZE, 8, EGL_GREEN_SIZE, 8, EGL_BLUE_SIZE, 8, EGL_ALPHA_SIZE, 8, EGL_NONE};
    EGLConfig config;
    EGLint count = 0, context_attributes[] = {EGL_CONTEXT_CLIENT_VERSION, 2, EGL_NONE};
    if (!eglChooseConfig(display, attributes, &config, 1, &count) || count != 1) goto cleanup;
    context = eglCreateContext(display, config, EGL_NO_CONTEXT, context_attributes);
    surface = eglCreateWindowSurface(display, config, (EGLNativeWindowType)a.window, NULL);
    if (context == EGL_NO_CONTEXT || surface == EGL_NO_SURFACE || !eglMakeCurrent(display, surface, surface, context)) goto cleanup;
    phase = "hardware-renderer";
    const char *renderer = (const char *)glGetString(GL_RENDERER);
    if (software_renderer(renderer)) goto cleanup;
    phase = "shader";
    vertex = shader(GL_VERTEX_SHADER, "attribute vec2 p;void main(){gl_Position=vec4(p,0.0,1.0);}");
    fragment = shader(GL_FRAGMENT_SHADER, "precision mediump float;void main(){gl_FragColor=vec4(0.0,1.0,0.0,1.0);}");
    if (!vertex || !fragment) goto cleanup;
    program = glCreateProgram(); glAttachShader(program, vertex); glAttachShader(program, fragment);
    glBindAttribLocation(program, 0, "p"); glLinkProgram(program);
    GLint linked = GL_FALSE; glGetProgramiv(program, GL_LINK_STATUS, &linked);
    if (linked != GL_TRUE) goto cleanup;
    glUseProgram(program); glDisable(GL_DITHER);
    const GLfloat triangle[] = {-0.75f,-0.75f, 0.75f,-0.75f, 0.0f,0.75f};
    const unsigned char expected_center[] = {0,255,0,255}, expected_corner[] = {16,32,64,255};
    unsigned char center[4], corner[4];
    phase = "draw-readback-present";
    for (int frame = 0; frame < 3; frame++) {
        if (wl_display_dispatch_pending(a.display) < 0 || a.closed) goto cleanup;
        glViewport(0, 0, a.width, a.height); glClearColor(16.0f/255.0f, 32.0f/255.0f, 64.0f/255.0f, 1.0f);
        glClear(GL_COLOR_BUFFER_BIT); glVertexAttribPointer(0, 2, GL_FLOAT, GL_FALSE, 0, triangle);
        glEnableVertexAttribArray(0); glDrawArrays(GL_TRIANGLES, 0, 3); glFinish();
        glReadPixels(a.width/2, a.height/2, 1, 1, GL_RGBA, GL_UNSIGNED_BYTE, center);
        glReadPixels(1, 1, 1, 1, GL_RGBA, GL_UNSIGNED_BYTE, corner);
        if (glGetError() != GL_NO_ERROR || !near_pixel(center, expected_center) || !near_pixel(corner, expected_corner)) goto cleanup;
        if (!eglSwapBuffers(display, surface)) goto cleanup;
    }
    sleep(3); /* Keep the verified image visible without submitting more work. */
    printf("{\"status\":\"PASS\",\"native_wayland\":true,\"api\":\"OpenGL ES2\",\"frames\":3,\"known_pixels\":true,\"renderer\":");
    json_string(renderer);
    printf(",\"width\":%d,\"height\":%d,\"hardware_acceptance\":false}\n", a.width, a.height);
    status = 0;
cleanup:
    if (status) printf("{\"status\":\"FAIL\",\"phase\":\"%s\",\"egl_error\":%u,\"hardware_acceptance\":false}\n", phase, eglGetError());
    if (program) glDeleteProgram(program);
    if (vertex) glDeleteShader(vertex);
    if (fragment) glDeleteShader(fragment);
    if (display != EGL_NO_DISPLAY) {
        eglMakeCurrent(display, EGL_NO_SURFACE, EGL_NO_SURFACE, EGL_NO_CONTEXT);
        if (surface != EGL_NO_SURFACE) eglDestroySurface(display, surface);
        if (context != EGL_NO_CONTEXT) eglDestroyContext(display, context);
        eglTerminate(display);
    }
    if (a.window) wl_egl_window_destroy(a.window);
    if (a.toplevel) xdg_toplevel_destroy(a.toplevel);
    if (a.xdg_surface) xdg_surface_destroy(a.xdg_surface);
    if (a.surface) wl_surface_destroy(a.surface);
    if (a.wm) xdg_wm_base_destroy(a.wm);
    if (a.compositor) wl_compositor_destroy(a.compositor);
    if (a.display) wl_display_disconnect(a.display);
    return status;
}
