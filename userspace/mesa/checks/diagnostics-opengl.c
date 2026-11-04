// SPDX-License-Identifier: MIT
/* Finite surfaceless GLES reference image. Device access requires --run. */
#define _POSIX_C_SOURCE 200809L
#include <EGL/egl.h>
#include <EGL/eglext.h>
#include <GLES2/gl2.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

enum { WIDTH = 64, HEIGHT = 64 };

static void fail(const char *stage)
{
    fprintf(stderr, "STOP %s EGL=0x%x GL=0x%x; no automatic retry or reset\n",
            stage, eglGetError(), glGetError());
    fflush(NULL);
    exit(1);
}

static GLuint shader(GLenum type, const char *text)
{
    GLuint result = glCreateShader(type);
    glShaderSource(result, 1, &text, NULL);
    glCompileShader(result);
    GLint compiled = 0;
    glGetShaderiv(result, GL_COMPILE_STATUS, &compiled);
    if (!compiled) {
        char log[2048];
        glGetShaderInfoLog(result, sizeof(log), NULL, log);
        fprintf(stderr, "%s\n", log);
        fail("compile shader");
    }
    return result;
}

int main(int argc, char **argv)
{
    if (argc != 2 || strcmp(argv[1], "--run")) {
        puts("Usage: psx-opengl-probe --run\nNo device access without --run.");
        return argc == 2 && !strcmp(argv[1], "--help") ? 0 : 2;
    }
    PFNEGLGETPLATFORMDISPLAYEXTPROC platform_display =
        (PFNEGLGETPLATFORMDISPLAYEXTPROC)eglGetProcAddress("eglGetPlatformDisplayEXT");
    if (!platform_display)
        fail("surfaceless display entry point");
    EGLDisplay display = platform_display(EGL_PLATFORM_SURFACELESS_MESA, EGL_DEFAULT_DISPLAY, NULL);
    EGLint major = 0, minor = 0;
    if (display == EGL_NO_DISPLAY || !eglInitialize(display, &major, &minor))
        fail("initialize EGL");
    if (!eglBindAPI(EGL_OPENGL_ES_API))
        fail("bind GLES");
    const EGLint attributes[] = {EGL_SURFACE_TYPE, EGL_PBUFFER_BIT,
        EGL_RENDERABLE_TYPE, EGL_OPENGL_ES2_BIT, EGL_RED_SIZE, 8, EGL_GREEN_SIZE, 8,
        EGL_BLUE_SIZE, 8, EGL_ALPHA_SIZE, 8, EGL_SAMPLES, 0, EGL_NONE};
    EGLConfig config;
    EGLint count = 0;
    if (!eglChooseConfig(display, attributes, &config, 1, &count) || count != 1)
        fail("choose RGBA pbuffer");
    const EGLint context_attributes[] = {EGL_CONTEXT_CLIENT_VERSION, 2, EGL_NONE};
    const EGLint surface_attributes[] = {EGL_WIDTH, WIDTH, EGL_HEIGHT, HEIGHT, EGL_NONE};
    EGLContext context = eglCreateContext(display, config, EGL_NO_CONTEXT, context_attributes);
    EGLSurface surface = eglCreatePbufferSurface(display, config, surface_attributes);
    if (context == EGL_NO_CONTEXT || surface == EGL_NO_SURFACE ||
        !eglMakeCurrent(display, surface, surface, context))
        fail("create current context");
    const char *renderer = (const char *)glGetString(GL_RENDERER);
    const char *vendor = (const char *)glGetString(GL_VENDOR);
    const char *version = (const char *)glGetString(GL_VERSION);
    printf("VENDOR %s\nRENDERER %s\nVERSION %s\n", vendor ? vendor : "unknown",
           renderer ? renderer : "unknown", version ? version : "unknown");
    fflush(stdout);
    const int software = renderer && (strstr(renderer, "llvmpipe") || strstr(renderer, "softpipe") ||
                                     strstr(renderer, "Software") || strstr(renderer, "SwiftShader"));
    const int amd = renderer && (strstr(renderer, "AMD") || strstr(renderer, "Radeon") ||
                                strstr(renderer, "Liverpool") || strstr(renderer, "Gladius"));
    GLuint vertex = shader(GL_VERTEX_SHADER,
        "attribute vec2 p;void main(){gl_Position=vec4(p,0.,1.);}");
    GLuint fragment = shader(GL_FRAGMENT_SHADER,
        "precision mediump float;void main(){gl_FragColor=vec4(0.,1.,0.,1.);}");
    GLuint program = glCreateProgram();
    glAttachShader(program, vertex);
    glAttachShader(program, fragment);
    glBindAttribLocation(program, 0, "p");
    glLinkProgram(program);
    GLint linked = 0;
    glGetProgramiv(program, GL_LINK_STATUS, &linked);
    if (!linked)
        fail("link program");
    glUseProgram(program);
    const GLfloat vertices[] = {-1.f, -1.f, 3.f, -1.f, -1.f, 3.f};
    GLuint buffer;
    glGenBuffers(1, &buffer);
    glBindBuffer(GL_ARRAY_BUFFER, buffer);
    glBufferData(GL_ARRAY_BUFFER, sizeof(vertices), vertices, GL_STATIC_DRAW);
    glEnableVertexAttribArray(0);
    glVertexAttribPointer(0, 2, GL_FLOAT, GL_FALSE, 0, NULL);
    glViewport(0, 0, WIDTH, HEIGHT);
    glClearColor(1.f, 0.f, 0.f, 1.f);
    glClear(GL_COLOR_BUFFER_BIT);
    glDrawArrays(GL_TRIANGLES, 0, 3);
    unsigned char pixels[WIDTH * HEIGHT * 4];
    memset(pixels, 0x55, sizeof(pixels));
    glReadPixels(0, 0, WIDTH, HEIGHT, GL_RGBA, GL_UNSIGNED_BYTE, pixels);
    if (glGetError() != GL_NO_ERROR)
        fail("download pixels");
    unsigned mismatches = 0;
    for (unsigned i = 0; i < WIDTH * HEIGHT; ++i) {
        const unsigned char *pixel = pixels + i * 4;
        if (pixel[0] != 0 || pixel[1] != 255 || pixel[2] != 0 || pixel[3] != 255)
            ++mismatches;
    }
    printf("ORACLE pixels=%d mismatches=%u hardware_renderer=%d\n",
           WIDTH * HEIGHT, mismatches, amd && !software);
    glDeleteBuffers(1, &buffer);
    glDeleteProgram(program);
    glDeleteShader(vertex);
    glDeleteShader(fragment);
    eglMakeCurrent(display, EGL_NO_SURFACE, EGL_NO_SURFACE, EGL_NO_CONTEXT);
    eglDestroySurface(display, surface);
    eglDestroyContext(display, context);
    eglTerminate(display);
    if (mismatches)
        return 1;
    if (!amd || software) {
        fputs("Reference image matched, but a PS4 AMD hardware renderer was not established.\n", stderr);
        return 3;
    }
    puts("PASS hardware reference pixels; sustained graphics stability remains separate.");
    return 0;
}
