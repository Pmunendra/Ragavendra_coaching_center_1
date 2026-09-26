(function () {
  const root = document.documentElement;
  const key = "exam_portal_theme";
  const saved = localStorage.getItem(key);
  if (saved) root.setAttribute("data-bs-theme", saved);

  // Supports more than one toggle button on the same page (e.g. the
  // desktop navbar toggle and the mobile off-canvas menu toggle both
  // exist in the DOM at once — only one is visible at a time depending
  // on viewport, but both must stay in sync with the current theme).
  const buttons = document.querySelectorAll("#themeToggle, [data-theme-toggle]");
  if (!buttons.length) return;

  const syncIcons = () => {
    const isDark = root.getAttribute("data-bs-theme") === "dark";
    buttons.forEach((btn) => {
      const icon = btn.querySelector("i");
      if (!icon) return;
      icon.classList.toggle("bi-moon-stars", !isDark);
      icon.classList.toggle("bi-sun-fill", isDark);
    });
  };
  syncIcons();

  buttons.forEach((btn) => {
    btn.addEventListener("click", function () {
      const current = root.getAttribute("data-bs-theme") === "dark" ? "light" : "dark";
      root.setAttribute("data-bs-theme", current);
      localStorage.setItem(key, current);
      syncIcons();
    });
  });
})();
