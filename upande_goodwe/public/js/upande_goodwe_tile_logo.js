// Replays the Solar & Water app mark on hover/focus in the desk.
//
// upande-goodwe-icon.svg animates itself once when it loads (Upande -> GoodWe -> HydraX -> Upande)
// and rests as the plain Upande mark. An <img> can't be told to replay, so on mouseenter/focusin
// the SVG text (fetched once) is handed back to the same <img> as a fresh Blob URL, which restarts
// the CSS animation. The <img> keeps its size; old Blob URLs are revoked. With reduced motion
// nothing is touched. Same approach as upande_production's upande_tile_logo.js.
(function () {
	var ICON = /^\/assets\/upande_goodwe\/images\/upande-goodwe-(icon|logo)\.svg(\?.*)?$/;

	if (window.__upandeGoodweTileLogo) return;
	window.__upandeGoodweTileLogo = true;
	if (window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
	if (!window.fetch || !window.URL || !URL.createObjectURL || !window.Blob) return;

	var cache = {};

	function load(src) {
		if (!cache[src]) {
			cache[src] = fetch(src, { credentials: "same-origin" }).then(function (r) {
				if (!r.ok) throw new Error(r.status);
				return r.text();
			});
			cache[src].catch(function () {
				delete cache[src];
			});
		}
		return cache[src];
	}

	function path(src) {
		try {
			return new URL(src, window.location.href).pathname;
		} catch (_) {
			return "";
		}
	}

	function replay(img) {
		var src = img.getAttribute("data-ugw-src");
		if (!src) return;
		load(src)
			.then(function (text) {
				if (!img.isConnected) return;
				var old = img.getAttribute("data-ugw-play");
				var url = URL.createObjectURL(new Blob([text], { type: "image/svg+xml" }));
				img.setAttribute("data-ugw-play", url);
				img.src = url;
				if (old) URL.revokeObjectURL(old);
			})
			.catch(function () {
				// keep the resting mark
			});
	}

	function wire(img) {
		if (img.getAttribute("data-ugw-src")) return;
		var p = path(img.getAttribute("src") || "");
		if (!ICON.test(p)) return;
		img.setAttribute("data-ugw-src", p);
		var target = img.closest(".desktop-icon, .app-item, a") || img.parentElement || img;
		var on = function () {
			replay(img);
		};
		target.addEventListener("mouseenter", on);
		target.addEventListener("focusin", on);
		load(p).catch(function () {});
	}

	function scan() {
		var imgs = document.querySelectorAll("img[src*='upande-goodwe-']");
		for (var i = 0; i < imgs.length; i++) wire(imgs[i]);
	}

	var queued = false;
	function onMutate() {
		if (queued) return;
		queued = true;
		requestAnimationFrame(function () {
			queued = false;
			scan();
		});
	}
	function start() {
		scan();
		new MutationObserver(onMutate).observe(document.body, { childList: true, subtree: true });
	}
	if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start);
	else start();
})();
