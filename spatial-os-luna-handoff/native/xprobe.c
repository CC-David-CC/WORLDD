/* Independent X11 drawing/input witness. No production Spatial OS headers.
 * Build with -DSPATIAL_HAVE_XI2 for the required multi-actor tests.
 * stdin commands: sync N; paint N R G B; quadrants N; resize N W H;
 *                map N; unmap N; geometry N; popup N W H; survivor N W H;
 *                destroy N; quit N.
 * All observations are JSON lines on stdout. Keep stderr separate.
 */
#define _POSIX_C_SOURCE 200809L
#include <X11/Xlib.h>
#include <X11/Xutil.h>
#include <X11/Xatom.h>
#include <X11/keysym.h>
#ifdef SPATIAL_HAVE_XI2
#include <X11/extensions/XInput2.h>
#endif
#include <errno.h>
#include <poll.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

static Display *dpy;
static Window win;
static GC gc;
static const char *probe_id;
static int width=320, height=240, live=1, use_xi=0;
#ifdef SPATIAL_HAVE_XI2
static int xi_opcode=-1;
#endif
static unsigned long color_rgb(unsigned int r,unsigned int g,unsigned int b) {
    XColor c; c.red=(unsigned short)(r*257); c.green=(unsigned short)(g*257); c.blue=(unsigned short)(b*257);
    c.flags=DoRed|DoGreen|DoBlue;
    if(!XAllocColor(dpy,DefaultColormap(dpy,DefaultScreen(dpy)),&c)) {fprintf(stderr,"XAllocColor failed\n");exit(3);}
    return c.pixel;
}
static void rect(Window w,int x,int y,unsigned int sx,unsigned int sy,unsigned long color) {
    XSetForeground(dpy,gc,color); XFillRectangle(dpy,w,gc,x,y,sx,sy);
}
static int solid=0;
static unsigned int sr=0,sg=0,sb=0;
static void draw(void) {
    if(!live) return;
    if(solid) rect(win,0,0,(unsigned)width,(unsigned)height,color_rgb(sr,sg,sb));
    else {
        int hw=width/2,hh=height/2;
        rect(win,0,0,(unsigned)hw,(unsigned)hh,color_rgb(255,0,0));
        rect(win,hw,0,(unsigned)(width-hw),(unsigned)hh,color_rgb(0,255,0));
        rect(win,0,hh,(unsigned)hw,(unsigned)(height-hh),color_rgb(0,0,255));
        rect(win,hw,hh,(unsigned)(width-hw),(unsigned)(height-hh),color_rgb(255,255,255));
    }
    XFlush(dpy);
}
static int xerror(Display *display,XErrorEvent *e) {
    char message[256]; XGetErrorText(display,e->error_code,message,sizeof message);
    fprintf(stderr,"X error %u request %u minor %u: %s\n",e->error_code,e->request_code,e->minor_code,message);
    exit(4);
}
static unsigned char channel(unsigned long pixel,unsigned long mask) {
    if(!mask) return 0;
    int shift=0; while(!(mask&1UL)){mask>>=1; shift++;}
    unsigned long value=(pixel>>shift)&mask;
    return (unsigned char)((value*255UL+mask/2UL)/mask);
}
static int capture(Window target,const char *path) {
    XWindowAttributes a;
    if(!XGetWindowAttributes(dpy,target,&a)) return 5;
    XSync(dpy,False);
    XImage *im=XGetImage(dpy,target,0,0,(unsigned)a.width,(unsigned)a.height,AllPlanes,ZPixmap);
    if(!im) return 5;
    FILE *fp=fopen(path,"wb"); if(!fp){perror(path);XDestroyImage(im);return 5;}
    fprintf(fp,"P6\n%d %d\n255\n",a.width,a.height);
    for(int y=0;y<a.height;y++) for(int x=0;x<a.width;x++) {
        unsigned long p=XGetPixel(im,x,y);
        unsigned char rgb[]={channel(p,im->red_mask),channel(p,im->green_mask),channel(p,im->blue_mask)};
        if(fwrite(rgb,1,3,fp)!=3){fclose(fp);XDestroyImage(im);return 5;}
    }
    int result=fclose(fp);XDestroyImage(im);return result?5:0;
}
static void report_key(int pressed,unsigned int code,unsigned int state,int device,Window target) {
    XKeyEvent k; memset(&k,0,sizeof k); k.display=dpy;k.window=target;k.keycode=code;k.state=state;
    k.type=pressed?KeyPress:KeyRelease;
    KeySym sym=NoSymbol; char text[64]; int n=XLookupString(&k,text,sizeof text,&sym,NULL);
    printf("{\"event\":\"key\",\"pressed\":%s,\"keycode\":%u,\"keysym\":%lu,\"device\":%d,\"xid\":%lu,\"text_hex\":\"",pressed?"true":"false",code,(unsigned long)sym,device,target);
    for(int i=0;i<n;i++) printf("%02x",(unsigned char)text[i]);
    puts("\"}");
}
static void select_events(Window w) {
    long mask=ExposureMask|StructureNotifyMask|PropertyChangeMask;
    if(!use_xi) mask|=ButtonPressMask|ButtonReleaseMask|KeyPressMask|KeyReleaseMask|PointerMotionMask|FocusChangeMask;
    XSelectInput(dpy,w,mask);
#ifdef SPATIAL_HAVE_XI2
    if(use_xi) {
        unsigned char maskbits[XIMaskLen(XI_LASTEVENT)];memset(maskbits,0,sizeof maskbits);
        XISetMask(maskbits,XI_ButtonPress);XISetMask(maskbits,XI_ButtonRelease);
        XISetMask(maskbits,XI_KeyPress);XISetMask(maskbits,XI_KeyRelease);
        XISetMask(maskbits,XI_Motion);XISetMask(maskbits,XI_FocusIn);XISetMask(maskbits,XI_FocusOut);
        XIEventMask m={XIAllMasterDevices,(int)sizeof maskbits,maskbits};
        XISelectEvents(dpy,w,&m,1);
    }
#endif
}
static void event(XEvent *ev) {
    if(ev->type==Expose && ev->xexpose.window==win && ev->xexpose.count==0) draw();
    else if(ev->type==ConfigureNotify && ev->xconfigure.window==win) {
        width=ev->xconfigure.width;height=ev->xconfigure.height;
        printf("{\"event\":\"configure\",\"xid\":%lu,\"width\":%d,\"height\":%d}\n",win,width,height);draw();
    } else if(ev->type==MapNotify) printf("{\"event\":\"map\",\"xid\":%lu}\n",ev->xmap.window);
    else if(ev->type==UnmapNotify) printf("{\"event\":\"unmap\",\"xid\":%lu}\n",ev->xunmap.window);
    else if(ev->type==DestroyNotify) printf("{\"event\":\"destroy\",\"xid\":%lu}\n",ev->xdestroywindow.window);
    else if(ev->type==ButtonPress || ev->type==ButtonRelease)
        printf("{\"event\":\"button\",\"pressed\":%s,\"button\":%u,\"x\":%d,\"y\":%d,\"device\":0,\"xid\":%lu}\n",ev->type==ButtonPress?"true":"false",ev->xbutton.button,ev->xbutton.x,ev->xbutton.y,ev->xbutton.window);
    else if(ev->type==MotionNotify)
        printf("{\"event\":\"motion\",\"x\":%d,\"y\":%d,\"device\":0,\"xid\":%lu}\n",ev->xmotion.x,ev->xmotion.y,ev->xmotion.window);
    else if(ev->type==KeyPress || ev->type==KeyRelease) report_key(ev->type==KeyPress,ev->xkey.keycode,ev->xkey.state,0,ev->xkey.window);
    else if(ev->type==FocusIn || ev->type==FocusOut)
        printf("{\"event\":\"focus\",\"focused\":%s,\"device\":0,\"xid\":%lu}\n",ev->type==FocusIn?"true":"false",ev->xfocus.window);
#ifdef SPATIAL_HAVE_XI2
    else if(ev->type==GenericEvent && ev->xcookie.extension==xi_opcode && XGetEventData(dpy,&ev->xcookie)) {
        int t=ev->xcookie.evtype;
        if(t==XI_ButtonPress || t==XI_ButtonRelease || t==XI_Motion || t==XI_KeyPress || t==XI_KeyRelease) {
            XIDeviceEvent *e=(XIDeviceEvent*)ev->xcookie.data;
            if(t==XI_KeyPress || t==XI_KeyRelease) report_key(t==XI_KeyPress,(unsigned)e->detail,(unsigned)e->mods.effective,e->deviceid,e->event);
            else if(t==XI_Motion) printf("{\"event\":\"motion\",\"x\":%.9g,\"y\":%.9g,\"device\":%d,\"xid\":%lu}\n",e->event_x,e->event_y,e->deviceid,e->event);
            else printf("{\"event\":\"button\",\"pressed\":%s,\"button\":%d,\"x\":%.9g,\"y\":%.9g,\"device\":%d,\"xid\":%lu}\n",t==XI_ButtonPress?"true":"false",e->detail,e->event_x,e->event_y,e->deviceid,e->event);
        } else if(t==XI_FocusIn || t==XI_FocusOut) {
            XIEnterEvent *e=(XIEnterEvent*)ev->xcookie.data;
            printf("{\"event\":\"focus\",\"focused\":%s,\"device\":%d,\"xid\":%lu}\n",t==XI_FocusIn?"true":"false",e->deviceid,e->event);
        }
        XFreeEventData(dpy,&ev->xcookie);
    }
#endif
}
static void command(char *line) {
    char op[32]={0}; unsigned int seq=0; int a=0,b=0,c=0;
    int fields=sscanf(line,"%31s %u %d %d %d",op,&seq,&a,&b,&c);
    if(fields<2){fprintf(stderr,"invalid probe command\n");exit(6);}
    if(!strcmp(op,"paint") && fields==5 && a>=0 && a<=255 && b>=0 && b<=255 && c>=0 && c<=255) {
        solid=1;sr=(unsigned)a;sg=(unsigned)b;sb=(unsigned)c;draw();
    } else if(!strcmp(op,"quadrants")){solid=0;draw();}
    else if(!strcmp(op,"resize") && fields>=4 && a>0 && b>0) XResizeWindow(dpy,win,(unsigned)a,(unsigned)b);
    else if(!strcmp(op,"unmap")) XUnmapWindow(dpy,win);
    else if(!strcmp(op,"map")) XMapWindow(dpy,win);
    else if(!strcmp(op,"destroy")){XDestroyWindow(dpy,win);live=0;}
    else if(!strcmp(op,"quit")){XCloseDisplay(dpy);exit(0);}
    else if(!strcmp(op,"geometry")) {
        XWindowAttributes attr;Window child;int x,y;
        XGetWindowAttributes(dpy,win,&attr);XTranslateCoordinates(dpy,win,DefaultRootWindow(dpy),0,0,&x,&y,&child);
        printf("{\"event\":\"geometry\",\"seq\":%u,\"x\":%d,\"y\":%d,\"width\":%d,\"height\":%d}\n",seq,x,y,attr.width,attr.height);
    } else if(!strcmp(op,"popup") && fields>=4 && a>0 && b>0) {
        XSetWindowAttributes attr;memset(&attr,0,sizeof attr);attr.override_redirect=True;
        Window popup=XCreateWindow(dpy,DefaultRootWindow(dpy),30,40,(unsigned)a,(unsigned)b,0,CopyFromParent,InputOutput,CopyFromParent,CWOverrideRedirect,&attr);
        XSetTransientForHint(dpy,popup,win);select_events(popup);XMapWindow(dpy,popup);
        rect(popup,0,0,(unsigned)a,(unsigned)b,color_rgb(255,255,0));
        printf("{\"event\":\"popup\",\"seq\":%u,\"xid\":%lu,\"parent_xid\":%lu}\n",seq,popup,win);
    } else if(!strcmp(op,"survivor") && fields>=4 && a>0 && b>0) {
        XSetWindowAttributes attr;memset(&attr,0,sizeof attr);attr.override_redirect=True;
        Window popup=XCreateWindow(dpy,DefaultRootWindow(dpy),30,40,(unsigned)a,(unsigned)b,0,CopyFromParent,InputOutput,CopyFromParent,CWOverrideRedirect,&attr);
        Atom prop=XInternAtom(dpy,"_SPATIAL_PROBE_ID",False),utf8=XInternAtom(dpy,"UTF8_STRING",False);
        char child_id[256];snprintf(child_id,sizeof child_id,"%s-survivor",probe_id);
        XChangeProperty(dpy,popup,prop,utf8,8,PropModeReplace,(const unsigned char*)child_id,(int)strlen(child_id));
        select_events(popup);XMapWindow(dpy,popup);rect(popup,0,0,(unsigned)a,(unsigned)b,color_rgb(255,255,0));
        printf("{\"event\":\"survivor\",\"seq\":%u,\"xid\":%lu,\"probe_id\":\"%s\"}\n",seq,popup,child_id);
    } else if(strcmp(op,"sync")) {fprintf(stderr,"unsupported/invalid probe command: %s\n",line);exit(6);}
    XSync(dpy,False);
    while(XPending(dpy)){XEvent e;XNextEvent(dpy,&e);event(&e);}
    XSync(dpy,False);
    printf("{\"event\":\"ack\",\"seq\":%u}\n",seq);
}
int main(int argc,char **argv) {
    const char *id="probe";const char *capture_path=NULL;Window capture_target=0;
    for(int i=1;i<argc;i++) {
        if(!strcmp(argv[i],"--id") && i+1<argc) id=argv[++i];
        else if(!strcmp(argv[i],"--xi2")) use_xi=1;
        else if(!strcmp(argv[i],"--capture-window") && i+1<argc) capture_target=strtoul(argv[++i],NULL,0);
        else if(!strcmp(argv[i],"--output") && i+1<argc) capture_path=argv[++i];
        else {fprintf(stderr,"unknown argument %s\n",argv[i]);return 2;}
    }
    for(const char *p=id;*p;p++) if(!((*p>='a'&&*p<='z')||(*p>='A'&&*p<='Z')||(*p>='0'&&*p<='9')||*p=='-'||*p=='_')){fprintf(stderr,"probe id must be ASCII identifier\n");return 2;}
    probe_id=id;
    setvbuf(stdout,NULL,_IOLBF,0);
    dpy=XOpenDisplay(NULL);if(!dpy){fprintf(stderr,"cannot open DISPLAY\n");return 2;}
    XSetErrorHandler(xerror);
    if(capture_path){int rc=capture(capture_target?capture_target:DefaultRootWindow(dpy),capture_path);XCloseDisplay(dpy);return rc;}
#ifdef SPATIAL_HAVE_XI2
    if(use_xi){int eventbase,errorbase,major=2,minor=0;
        if(!XQueryExtension(dpy,"XInputExtension",&xi_opcode,&eventbase,&errorbase)||XIQueryVersion(dpy,&major,&minor)==BadRequest){fprintf(stderr,"XI2 unavailable\n");return 2;}}
#else
    if(use_xi){fprintf(stderr,"probe not built with XI2; run tools/build_probe.py --xi2\n");return 2;}
#endif
    win=XCreateSimpleWindow(dpy,DefaultRootWindow(dpy),0,0,(unsigned)width,(unsigned)height,0,0,0);
    gc=XCreateGC(dpy,win,0,NULL);XStoreName(dpy,win,id);
    Atom prop=XInternAtom(dpy,"_SPATIAL_PROBE_ID",False),utf8=XInternAtom(dpy,"UTF8_STRING",False);
    XChangeProperty(dpy,win,prop,utf8,8,PropModeReplace,(const unsigned char*)id,(int)strlen(id));
    XClassHint hint;hint.res_name=(char*)id;hint.res_class="SpatialProbe";XSetClassHint(dpy,win,&hint);
    select_events(win);XMapWindow(dpy,win);draw();XSync(dpy,False);
    printf("{\"event\":\"ready\",\"xid\":%lu,\"pid\":%ld,\"width\":%d,\"height\":%d,\"xi2\":%s}\n",win,(long)getpid(),width,height,use_xi?"true":"false");
    char input[4096];size_t used=0;
    for(;;){
        while(XPending(dpy)){XEvent e;XNextEvent(dpy,&e);event(&e);}
        struct pollfd fds[2]={{ConnectionNumber(dpy),POLLIN,0},{STDIN_FILENO,POLLIN,0}};
        int rc=poll(fds,2,-1);if(rc<0){if(errno==EINTR)continue;perror("poll");return 2;}
        if(fds[1].revents&(POLLIN|POLLHUP)){
            char buf[512];ssize_t n=read(STDIN_FILENO,buf,sizeof buf);
            if(n<=0){XCloseDisplay(dpy);return 0;}
            for(ssize_t i=0;i<n;i++){
                if(buf[i]=='\n'){input[used]='\0';command(input);used=0;}
                else {if(used+1>=sizeof input){fprintf(stderr,"probe command too large\n");return 2;}input[used++]=buf[i];}
            }
        }
    }
}
