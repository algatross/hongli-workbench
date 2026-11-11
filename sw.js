/* 红利买点工作台 · Service Worker
 *
 * 作用只有两个：
 *   1. 断网 / 弱网时，用上次缓存的页面兜底（地铁、电梯里也能看上次的数据）
 *   2. 在线时永远走网络，保证拿到的是最新版本
 *
 * 刻意不做的事：行情（qt.gtimg.cn）和分红（eastmoney）接口一律不缓存、不拦截，
 * 这两个数据必须每次都是新的，缓存了就是害人。
 *
 * 改完 index.html 发版时，把 CACHE 版本号改一下，否则老缓存可能被反复命中。
 */
var CACHE = "hongli-v1.9-20261111";

var SHELL = [
  "./",
  "./index.html",
  "./manifest.webmanifest",
  "./icons/icon-180.png",
  "./icons/icon-192.png",
  "./icons/icon-512.png"
];

self.addEventListener("install", function (e) {
  e.waitUntil(
    caches.open(CACHE)
      .then(function (c) { return c.addAll(SHELL); })
      .catch(function () { /* 个别文件缺失不影响安装 */ })
      .then(function () { return self.skipWaiting(); })
  );
});

self.addEventListener("activate", function (e) {
  e.waitUntil(
    caches.keys().then(function (keys) {
      return Promise.all(keys.map(function (k) {
        return k === CACHE ? null : caches.delete(k);
      }));
    }).then(function () { return self.clients.claim(); })
  );
});

self.addEventListener("fetch", function (e) {
  var req = e.request;
  if (req.method !== "GET") return;

  var url = new URL(req.url);

  /* 跨域（行情 / 分红接口）完全放行，交给浏览器直连 */
  if (url.origin !== self.location.origin) return;

  /* boll.json 是每日更新的数据，而且页面用 ?t=时间戳 破缓存。
     如果让它进缓存，一是会拿到旧轨道，二是每次打开都会堆一条重复条目。 */
  if (/\.json$/i.test(url.pathname)) return;

  e.respondWith(
    fetch(req).then(function (res) {
      if (res && res.ok && res.type === "basic") {
        var copy = res.clone();
        caches.open(CACHE).then(function (c) {
          c.put(req, copy).catch(function () {});
        });
      }
      return res;
    }).catch(function () {
      return caches.match(req).then(function (hit) {
        return hit || caches.match("./index.html");
      });
    })
  );
});
