/* liquid-glass.js — 液态玻璃界面效果（原生 JS 实现）
 * 结构与参数移植自 liquid-glass-vue（@wxperia/liquid-glass-vue v1.0.9, MIT License）
 * 原理：宿主元素内注入 <svg>（SVG 折射+色散滤镜定义）与 <span class="lg-warp">
 *       （backdrop-filter 磨砂层 + filter:url() 折射），内容层提升 z-index 保持清晰。
 * Firefox 降级：跳过 SVG 折射，仅保留磨砂。
 * 依赖：lg-maps.js（位移图）需先于本文件加载。
 */
(function () {
  "use strict";
  var NS = "http://www.w3.org/2000/svg";
  var maps = window.__LG_MAPS;
  if (!maps) return;
  var isFirefox = /firefox/i.test(navigator.userAgent);
  var seq = 0;

  /* 各类元素的玻璃参数（scale=折射强度, blur=磨砂模糊px, sat=饱和度%, map=位移图） */
  var PRESETS = [
    [".sidebar",  { scale: 90, blur: 16, sat: 150 }],
    [".card",     { scale: 70, blur: 10, sat: 145 }],
    /* drawer 贴屏右缘：横向折射向界外空隙采样，暗带宽≈scale/2+blur。实测 scale=50 时
       暗带 27px 且侵入内容区（x=1240 处 L 跌到 0.52），scale=25（原库默认档）收到 20px
       贴边、内容区 ≥0.74。modal 居中、四周采样真实内容无空隙暗带，维持 50 不变 */
    [".drawer",   { scale: 25, blur: 10, sat: 170 }],
    [".modal",    { scale: 50, blur: 10, sat: 170 }],
    [".nav-item", { scale: 45, blur: 6,  sat: 150, map: "polarDisplacementMap" }],
    [".btn",      { scale: 45, blur: 6,  sat: 150, map: "polarDisplacementMap" }],
    [".chip-btn", { scale: 40, blur: 6,  sat: 160, map: "polarDisplacementMap" }]
  ];

  function mkSvg(tag, attrs, parent) {
    var n = document.createElementNS(NS, tag);
    for (var k in attrs) n.setAttribute(k, attrs[k]);
    if (parent) parent.appendChild(n);
    return n;
  }

  /* 按 GlassFilter.vue 模板生成滤镜（standard 模式：scale 取负，RGB 三通道错位色散） */
  function buildFilter(id, o) {
    var mapUrl = maps[o.map] || maps.displacementMap;
    var ds = -Math.abs(o.scale);
    var ab = o.ab;
    var svg = mkSvg("svg", { "class": "lg-svg", "aria-hidden": "true", xmlns: NS });
    var defs = mkSvg("defs", {}, svg);
    var filter = mkSvg("filter", {
      id: id,
      x: "-35%", y: "-35%", width: "170%", height: "170%",
      "color-interpolation-filters": "sRGB"
    }, defs);
    function prim(tag, attrs) { return mkSvg(tag, attrs, filter); }

    prim("feImage", {
      x: "0", y: "0", width: "100%", height: "100%",
      result: "DISPLACEMENT_MAP", preserveAspectRatio: "xMidYMid slice", href: mapUrl
    });

    // 边缘遮罩：由位移图自身灰度生成（边缘强、中心透明）
    prim("feColorMatrix", {
      "in": "DISPLACEMENT_MAP", type: "matrix", result: "EDGE_INTENSITY",
      values: "0.3 0.3 0.3 0 0 0.3 0.3 0.3 0 0 0.3 0.3 0.3 0 0 0 0 0 1 0"
    });
    var ct1 = prim("feComponentTransfer", { "in": "EDGE_INTENSITY", result: "EDGE_MASK" });
    mkSvg("feFuncA", { type: "discrete", tableValues: "0 " + (ab * 0.05) + " 1" }, ct1);

    // 中心原图
    prim("feOffset", { "in": "SourceGraphic", dx: "0", dy: "0", result: "CENTER_ORIGINAL" });

    // 红通道折射（标准位移）
    prim("feDisplacementMap", {
      "in": "SourceGraphic", in2: "DISPLACEMENT_MAP", scale: String(ds),
      xChannelSelector: "R", yChannelSelector: "B", result: "RED_DISPLACED"
    });
    prim("feColorMatrix", {
      "in": "RED_DISPLACED", type: "matrix", result: "RED_CHANNEL",
      values: "1 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 1 0"
    });

    // 绿通道折射（略强，产生色散）
    prim("feDisplacementMap", {
      "in": "SourceGraphic", in2: "DISPLACEMENT_MAP", scale: String(ds * (1 + ab * 0.05)),
      xChannelSelector: "R", yChannelSelector: "B", result: "GREEN_DISPLACED"
    });
    prim("feColorMatrix", {
      "in": "GREEN_DISPLACED", type: "matrix", result: "GREEN_CHANNEL",
      values: "0 0 0 0 0 0 1 0 0 0 0 0 0 0 0 0 0 0 1 0"
    });

    // 蓝通道折射（更强）
    prim("feDisplacementMap", {
      "in": "SourceGraphic", in2: "DISPLACEMENT_MAP", scale: String(ds * (1 + ab * 0.1)),
      xChannelSelector: "R", yChannelSelector: "B", result: "BLUE_DISPLACED"
    });
    prim("feColorMatrix", {
      "in": "BLUE_DISPLACED", type: "matrix", result: "BLUE_CHANNEL",
      values: "0 0 0 0 0 0 0 0 0 0 0 0 1 0 0 0 0 0 1 0"
    });

    // screen 合成 RGB + 轻微模糊柔化
    prim("feBlend", { "in": "GREEN_CHANNEL", in2: "BLUE_CHANNEL", mode: "screen", result: "GB_COMBINED" });
    prim("feBlend", { "in": "RED_CHANNEL", in2: "GB_COMBINED", mode: "screen", result: "RGB_COMBINED" });
    prim("feGaussianBlur", {
      "in": "RGB_COMBINED", stdDeviation: String(Math.max(0.1, 0.5 - ab * 0.1)),
      result: "ABERRATED_BLURRED"
    });

    // 边缘用色散层，中心用未位移原图
    prim("feComposite", { "in": "ABERRATED_BLURRED", in2: "EDGE_MASK", operator: "in", result: "EDGE_ABERRATION" });
    var ct2 = prim("feComponentTransfer", { "in": "EDGE_MASK", result: "INVERTED_MASK" });
    mkSvg("feFuncA", { type: "table", tableValues: "1 0" }, ct2);
    prim("feComposite", { "in": "CENTER_ORIGINAL", in2: "INVERTED_MASK", operator: "in", result: "CENTER_CLEAN" });
    prim("feComposite", { "in": "EDGE_ABERRATION", in2: "CENTER_CLEAN", operator: "over" });

    return svg;
  }

  function presetFor(el) {
    var o = { scale: 70, blur: 10, sat: 150, ab: 2, map: "displacementMap" };
    for (var i = 0; i < PRESETS.length; i++) {
      if (el.matches(PRESETS[i][0])) {
        var p = PRESETS[i][1];
        for (var k in p) o[k] = p[k];
        break;
      }
    }
    if (el.dataset.glassScale) o.scale = +el.dataset.glassScale;
    if (el.dataset.glassBlur) o.blur = +el.dataset.glassBlur;
    if (el.dataset.glassSat) o.sat = +el.dataset.glassSat;
    if (el.dataset.glassMap) o.map = el.dataset.glassMap;
    return o;
  }

  function apply(el) {
    if (!el || el.__lgApplied) return;
    el.__lgApplied = true;
    // 裸文本包一层 span，才能被提升到折射层之上
    var kids = Array.prototype.slice.call(el.childNodes);
    for (var i = 0; i < kids.length; i++) {
      var n = kids[i];
      if (n.nodeType === 3 && n.nodeValue && n.nodeValue.trim()) {
        var w = document.createElement("span");
        w.className = "lg-inline";
        el.insertBefore(w, n);
        w.appendChild(n);
      }
    }
    var o = presetFor(el);
    el.classList.add("lg");
    // 仅在宿主无定位时补 relative（有 sticky/relative 的不覆盖）
    if (getComputedStyle(el).position === "static") el.style.position = "relative";
    var id = "lg-filter-" + (++seq);
    var svg = null;
    if (!isFirefox) {
      svg = buildFilter(id, o);
      el.insertBefore(svg, el.firstChild);
    }
    var warp = document.createElement("span");
    warp.className = "lg-warp";
    var bf = "blur(" + o.blur + "px) saturate(" + o.sat + "%)";
    warp.style.backdropFilter = bf;
    warp.style.webkitBackdropFilter = bf;
    if (!isFirefox) warp.style.filter = "url(#" + id + ")";
    el.insertBefore(warp, svg ? svg.nextSibling : el.firstChild);
  }

  var SEL = ".sidebar, .card, .drawer, .modal, .btn, .chip-btn, .nav-item";
  function scan() {
    var list = document.querySelectorAll(SEL);
    for (var i = 0; i < list.length; i++) apply(list[i]);
  }
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", scan);
  } else {
    scan();
  }
  window.LiquidGlass = { rescan: scan };
})();
