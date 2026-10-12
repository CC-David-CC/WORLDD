/* Independent ordinary Xlib client. It obtains its cookie only from Xauthority. */
#include <X11/Xlib.h>
#include <stdio.h>

static int failed;
static int error(Display *display, XErrorEvent *event)
{
    (void)display;
    fprintf(stderr, "Native X error %u on request %u\n", event->error_code, event->request_code);
    failed = 1;
    return 0;
}

int main(void)
{
    XSetErrorHandler(error);
    Display *display = XOpenDisplay(NULL);
    if (!display) return 3;
    Window window = XCreateSimpleWindow(display, DefaultRootWindow(display), 0, 0, 32, 32, 0, 0, 0);
    XMapWindow(display, window);
    XSync(display, False);
    XDestroyWindow(display, window);
    XSync(display, False);
    XCloseDisplay(display);
    return failed ? 4 : 0;
}
