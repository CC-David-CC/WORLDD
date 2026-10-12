/* Independent GLX context and pixel witness. Credentials come from Xauthority. */
#include <X11/Xlib.h>
#include <GL/gl.h>
#include <GL/glext.h>
#include <GL/glx.h>
#include <GL/glxext.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static int native_error;
static int error(Display *display, XErrorEvent *event)
{
    (void)display;
    fprintf(stderr, "X error %u request %u minor %u\n", event->error_code,
            event->request_code, event->minor_code);
    native_error = 1;
    return 0;
}

int main(int argc, char **argv)
{
    int cycles = argc == 2 ? atoi(argv[1]) : 32;
    if (cycles < 1 || cycles > 1000) return 2;
    XSetErrorHandler(error);
    Display *display = XOpenDisplay(NULL);
    if (!display) return 3;
    const int attributes[] = {GLX_X_RENDERABLE, True, GLX_DRAWABLE_TYPE, GLX_WINDOW_BIT,
        GLX_RENDER_TYPE, GLX_RGBA_BIT, GLX_X_VISUAL_TYPE, GLX_TRUE_COLOR,
        GLX_RED_SIZE, 8, GLX_GREEN_SIZE, 8, GLX_BLUE_SIZE, 8, GLX_DOUBLEBUFFER, True, None};
    int count = 0;
    GLXFBConfig *configs = glXChooseFBConfig(display, DefaultScreen(display), attributes, &count);
    if (!configs || !count) return 4;
    XVisualInfo *visual = glXGetVisualFromFBConfig(display, configs[0]);
    if (!visual) return 5;
    PFNGLXCREATECONTEXTATTRIBSARBPROC create = (PFNGLXCREATECONTEXTATTRIBSARBPROC)
        glXGetProcAddressARB((const GLubyte *)"glXCreateContextAttribsARB");
    if (!create) return 6;
    const int context_attributes[] = {GLX_CONTEXT_MAJOR_VERSION_ARB, 3,
        GLX_CONTEXT_MINOR_VERSION_ARB, 3, GLX_CONTEXT_PROFILE_MASK_ARB,
        GLX_CONTEXT_CORE_PROFILE_BIT_ARB, None};
    for (int cycle = 0; cycle < cycles; ++cycle) {
        XSetWindowAttributes window_attributes = {0};
        window_attributes.colormap = XCreateColormap(display, DefaultRootWindow(display), visual->visual, AllocNone);
        Window window = XCreateWindow(display, DefaultRootWindow(display), 0, 0, 32, 32, 0,
            visual->depth, InputOutput, visual->visual, CWColormap, &window_attributes);
        XMapWindow(display, window);
        GLXContext context = create(display, configs[0], NULL, True, context_attributes);
        XSync(display, False);
        if (!context || native_error || !glXMakeCurrent(display, window, context)) return 7;
        if (!glXIsDirect(display, context)) return 11;
        if (cycle == 0) {
            PFNGLGETSTRINGIPROC extension = (PFNGLGETSTRINGIPROC)
                glXGetProcAddressARB((const GLubyte *)"glGetStringi");
            if (!extension) return 12;
            GLint extension_count = 0;
            int fp64 = 0;
            glGetIntegerv(GL_NUM_EXTENSIONS, &extension_count);
            for (GLint i = 0; i < extension_count; ++i) {
                const GLubyte *name = extension(GL_EXTENSIONS, (GLuint)i);
                if (name && !strcmp((const char *)name, "GL_ARB_gpu_shader_fp64")) fp64 = 1;
            }
            if (!fp64) return 12;
            printf("renderer=%s version=%s direct=1 fp64=1\n", glGetString(GL_RENDERER),
                   glGetString(GL_VERSION));
        }
        unsigned char expected[3] = {(unsigned char)(31 + cycle % 127), 93, 177};
        glViewport(0, 0, 32, 32);
        glClearColor(expected[0] / 255.0f, expected[1] / 255.0f, expected[2] / 255.0f, 1.0f);
        glClear(GL_COLOR_BUFFER_BIT);
        glFinish();
        unsigned char actual[4] = {0};
        glReadPixels(16, 16, 1, 1, GL_RGBA, GL_UNSIGNED_BYTE, actual);
        for (int channel = 0; channel < 3; ++channel)
            if (abs((int)actual[channel] - expected[channel]) > 1) {
                fprintf(stderr, "Pixel mismatch cycle %d channel %d: %u != %u\n",
                        cycle, channel, actual[channel], expected[channel]);
                return 8;
            }
        if (glGetError() != GL_NO_ERROR) return 9;
        glXSwapBuffers(display, window);
        glXMakeCurrent(display, None, NULL);
        glXDestroyContext(display, context);
        XDestroyWindow(display, window);
        XFreeColormap(display, window_attributes.colormap);
        XSync(display, False);
        if (native_error) return 10;
    }
    XFree(visual);
    XFree(configs);
    XCloseDisplay(display);
    printf("cycles=%d pixel_checks=%d PASS\n", cycles, cycles);
    return 0;
}
