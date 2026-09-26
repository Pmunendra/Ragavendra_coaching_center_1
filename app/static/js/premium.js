// Kalyani Exam Hub — premium micro-interactions (Phase 2)
// Vanilla JS only, no external dependencies. Safe to include on every page;
// every block below is a no-op if its target elements aren't present.
(function () {
  "use strict";

  // ---- 1. Navbar: solid once the page is scrolled ----
  var nav = document.querySelector(".kh-navbar");
  if (nav) {
    var onScroll = function () {
      if (window.scrollY > 12) nav.classList.add("kh-scrolled");
      else nav.classList.remove("kh-scrolled");
    };
    onScroll();
    window.addEventListener("scroll", onScroll, { passive: true });
  }

  // ---- 2. Scroll reveal for anything marked [data-reveal] ----
  var revealEls = document.querySelectorAll("[data-reveal]");
  if (revealEls.length) {
    if ("IntersectionObserver" in window) {
      var io = new IntersectionObserver(
        function (entries) {
          entries.forEach(function (entry) {
            if (entry.isIntersecting) {
              entry.target.classList.add("kh-in-view");
              io.unobserve(entry.target);
            }
          });
        },
        { threshold: 0.12, rootMargin: "0px 0px -40px 0px" }
      );
      revealEls.forEach(function (el) { io.observe(el); });
    } else {
      revealEls.forEach(function (el) { el.classList.add("kh-in-view"); });
    }
  }

  // ---- 3. Ripple effect on buttons / nav pills ----
  document.addEventListener("click", function (e) {
    var btn = e.target.closest(".btn, .nav-link, .sidebar-link");
    if (!btn) return;
    var rect = btn.getBoundingClientRect();
    var ripple = document.createElement("span");
    var size = Math.max(rect.width, rect.height);
    ripple.className = "kh-ripple";
    ripple.style.width = ripple.style.height = size + "px";
    ripple.style.left = (e.clientX - rect.left - size / 2) + "px";
    ripple.style.top = (e.clientY - rect.top - size / 2) + "px";
    btn.appendChild(ripple);
    setTimeout(function () { ripple.remove(); }, 600);
  });
})();
