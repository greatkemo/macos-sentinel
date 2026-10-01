(function () {
  var stored = null;
  try { stored = localStorage.getItem("sentinel-theme"); } catch (error) { stored = null; }
  var theme = stored === "light" || stored === "dark" ? stored : "dark";
  var root = document.documentElement;
  root.dataset.theme = theme;
  root.style.colorScheme = theme;
  root.classList.toggle("dark", theme === "dark");
  var meta = document.querySelector('meta[name="color-scheme"]');
  if (meta) meta.content = theme;
})();
