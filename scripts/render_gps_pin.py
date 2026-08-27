import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageChops
import math, random
import mark_preview as mp

S = 8; IW, IH = 17, 24
W8, H8 = IW*S, IH*S
C = (68, 64); R = 58; T = (68, 186)

def teardrop_mask():
    cx, cy = C; tx, ty = T
    d = math.hypot(tx-cx, ty-cy)
    alpha = math.asin(R/d)
    beta = math.pi/2 - alpha
    pts = []
    n = 80
    a0 = math.pi/2 + beta
    a1 = math.pi/2 - beta + 2*math.pi
    for i in range(n+1):
        a = a0 + (a1-a0)*i/n
        pts.append((cx + R*math.cos(a), cy + R*math.sin(a)))
    pts.append((tx, ty))
    m = Image.new('L', (W8, H8), 0)
    ImageDraw.Draw(m).polygon(pts, fill=255)
    ImageDraw.Draw(m).ellipse([tx-6, ty-9, tx+6, ty+3], fill=255)
    return m

def lut(shade, ramp):
    xs = np.array([0.0, 0.5, 0.85, 1.0])
    cols = np.array(ramp, dtype=float)
    r = np.interp(shade, xs, cols[:,0]); g = np.interp(shade, xs, cols[:,1])
    b = np.interp(shade, xs, cols[:,2])
    return np.stack([r,g,b], -1)

def render(name, ramp, hole_grad, hole_wall):
    mask = teardrop_mask()
    m = np.asarray(mask, float)/255.0
    H = np.asarray(mask.filter(ImageFilter.GaussianBlur(14)), float)/255.0
    H = H**0.7
    gy, gx = np.gradient(H)
    k = 28.0
    nx, ny, nz = -gx*k, -gy*k, np.ones_like(H)
    nl = np.sqrt(nx*nx+ny*ny+nz*nz); nx, ny, nz = nx/nl, ny/nl, nz/nl
    L = np.array([-0.5, -0.6, 0.62]); L = L/np.linalg.norm(L)
    shade = np.clip(0.22 + 0.78*np.clip(nx*L[0]+ny*L[1]+nz*L[2], 0, 1), 0, 1)
    rgb = lut(shade, ramp)
    img = Image.fromarray((rgb * m[...,None]).astype(np.uint8), 'RGB')
    hx, hy = C[0], C[1]-4; hr = int(0.44*R)
    hole = Image.new('L', (W8,H8), 0)
    ImageDraw.Draw(hole).ellipse([hx-hr, hy-hr, hx+hr, hy+hr], fill=255)
    grad = np.linspace(0,1,H8)[:,None].repeat(W8,1)
    hole_rgb = (np.array(hole_grad[0])[None,None,:]*(1-grad[...,None])
                + np.array(hole_grad[1])[None,None,:]*grad[...,None]).astype(np.uint8)
    img.paste(Image.fromarray(hole_rgb,'RGB'), (0,0), hole)
    ic = Image.new('L',(W8,H8),0)
    ImageDraw.Draw(ic).ellipse([hx-hr, hy-hr+7, hx+hr, hy+hr+7], fill=255)
    crescent = ImageChops.subtract(hole, ic)
    dark = Image.new('RGB',(W8,H8),(0,0,0))
    img.paste(dark, (0,0), crescent.filter(ImageFilter.GaussianBlur(4)).point(lambda v: v*200//255))
    wall = Image.new('L',(W8,H8),0)
    ImageDraw.Draw(wall).arc([hx-hr+2, hy-hr+2, hx+hr-2, hy+hr-2], 30, 150, fill=255, width=6)
    img.paste(Image.new('RGB',(W8,H8),hole_wall), (0,0), wall.filter(ImageFilter.GaussianBlur(2)))
    ao = Image.new('L',(W8,H8),0)
    ImageDraw.Draw(ao).ellipse([hx-hr-8, hy-hr-8, hx+hr+8, hy+hr+8], outline=255, width=9)
    ao = ImageChops.subtract(ao, hole)
    aoc = tuple(int(c*0.5) for c in ramp[0])
    img.paste(Image.new('RGB',(W8,H8),aoc), (0,0),
              ao.filter(ImageFilter.GaussianBlur(6)).point(lambda v: v*70//255))
    px_target = (4, 4)
    scx, scy = 8*(px_target[0]+0.5), 8*(px_target[1]+0.5)
    spec = Image.new('L',(W8,H8),0)
    e = ImageDraw.Draw(spec)
    e.ellipse([scx-17, scy-10, scx+17, scy+10], fill=235)
    spec = spec.rotate(-35, center=(scx, scy)).filter(ImageFilter.GaussianBlur(3))
    img.paste(Image.new('RGB',(W8,H8),(255,255,255)), (0,0), spec)
    sheen = Image.new('L',(W8,H8),0)
    ImageDraw.Draw(sheen).ellipse([C[0]-int(1.0*R)+10, C[1]-int(0.85*R),
                                   C[0]+4, C[1]-int(0.1*R)], fill=55)
    img.paste(Image.new('RGB',(W8,H8),(255,255,255)), (0,0),
              sheen.filter(ImageFilter.GaussianBlur(20)))
    streak = Image.new('L',(W8,H8),0)
    sd = ImageDraw.Draw(streak)
    sd.line([(C[0]-14, C[1]+R-6), (T[0]-4, T[1]-18)], fill=80, width=9)
    img.paste(Image.new('RGB',(W8,H8),(255,255,255)), (0,0),
              streak.filter(ImageFilter.GaussianBlur(6)))
    ring = ImageChops.subtract(mask, mask.filter(ImageFilter.MinFilter(9)))
    ring_np = np.asarray(ring, float)/255.0
    yy, xx = np.mgrid[0:H8, 0:W8]
    ang = np.arctan2(yy-C[1], xx-C[0])
    ul = ((ang > -math.pi) & (ang < -math.pi/8)).astype(float)
    rim_hi = (ring_np * ul * 150).astype(np.uint8)
    hi_col = tuple(min(255, int(c*0.35+255*0.65)) for c in ramp[1])
    img.paste(Image.new('RGB',(W8,H8),hi_col), (0,0), Image.fromarray(rim_hi,'L'))
    lr = 1.0 - ul
    rim_lo = (ring_np * lr * 60).astype(np.uint8)
    lo_col = tuple(min(255,int(c*1.25)) for c in ramp[0])
    img.paste(Image.new('RGB',(W8,H8),lo_col), (0,0), Image.fromarray(rim_lo,'L'))
    out = Image.new('RGB',(W8,H8),(0,0,0)); out.paste(img,(0,0),mask)
    a = (np.asarray(out, float)/255.0)**2.2
    chans = []
    for ci in range(3):
        c8 = Image.fromarray((a[...,ci]*255).astype(np.uint8), 'L')
        chans.append(np.asarray(c8.resize((IW,IH), Image.LANCZOS), float)/255.0)
    smallf = np.clip(np.stack(chans,-1),0,1)**(1/2.2)
    small = Image.fromarray((smallf*255).astype(np.uint8),'RGB')
    small = small.filter(ImageFilter.UnsharpMask(radius=1, percent=60, threshold=0))
    am = np.asarray(mask.resize((IW,IH), Image.LANCZOS), float)/255.0
    outp = Image.new('RGB',(IW,IH),(0,0,0)); op = outp.load()
    sp = small.load()
    for y in range(IH):
        for x in range(IW):
            if am[y,x] < 0.25: continue
            r,g,b = sp[x,y]
            f = min(1.0, am[y,x]*1.15)
            r,g,b = int(r*f), int(g*f), int(b*f)
            if (r,g,b)==(0,0,0): r,g,b=(8,8,8)
            op[x,y]=(r,g,b)
    outp.save(f'pin_{name}_icon.png')
    return outp

red = render('red',
             ramp=[(0x4A,0x00,0x08),(0xD0,0x18,0x18),(0xFF,0x5A,0x4A),(0xFF,0xD8,0xCC)],
             hole_grad=((0x24,0x00,0x04),(0x4A,0x08,0x08)), hole_wall=(0xE8,0x60,0x50))
green = render('green',
             ramp=[(0x00,0x38,0x16),(0x12,0xA0,0x35),(0x5F,0xE0,0x7E),(0xD8,0xFF,0xE0)],
             hole_grad=((0x00,0x12,0x08),(0x0A,0x30,0x18)), hole_wall=(0x70,0xE8,0x90))

W,H,BAR = 160,80,5
for tag, icon in (("red", red), ("green", green)):
    img = Image.new('RGB',(W,H),'black'); d = ImageDraw.Draw(img)
    cx, cy = 80, (H-BAR)//2
    for rr in (34,33,31): d.ellipse([cx-rr,cy-rr,cx+rr,cy+rr], outline=mp.RING)
    d.ellipse([cx-5,cy-5,cx+5,cy+5], fill=mp.NODE)
    mp.draw_text(d, cx+5, cy-13, "RNode", (150,195,172), 1)
    d.rectangle([W-46,H-BAR-11,W-3,H-BAR-2], outline=(240,180,60))
    rng = random.Random(3)
    for _ in range(90):
        xx,yy = rng.randrange(W), rng.randrange(H-BAR); v=rng.randrange(90,170)
        d.point((xx,yy), fill=(v,v,v))
    ip = icon.load()
    for y in range(icon.height):
        for x in range(icon.width):
            c = ip[x,y]
            if c != (0,0,0): img.putpixel((W-19+x, 1+y), c)
    d.rectangle([W-40,H-BAR,W-1,H-1], fill=(90,170,60))
    img.resize((W*3,H*3), Image.NEAREST).save(f'tracker_pin3d_v4_{tag}.png')
strip = Image.new('RGB', (IW*2*8+24, IH*8), (12,12,14))
strip.paste(red.resize((IW*8, IH*8), Image.NEAREST), (0,0))
strip.paste(green.resize((IW*8, IH*8), Image.NEAREST), (IW*8+24,0))
strip.save('pin3d_v4_closeup.png')
print("v4 rendered")
