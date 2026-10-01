
    const $ = (id) => document.getElementById(id);
    const nativeFetch = window.fetch.bind(window);
    const token = document.querySelector('meta[name="sentinel-token"]').content;
    const fetch = (url, options = {}) => nativeFetch(url, {...options, headers: {...options.headers, 'X-Sentinel-Token': token}});
    const socket = url => new WebSocket(url, ['sentinel', token]);
    let currentTab = "overview";
    let processPage = 0;
    let lastSampleAt = 0;
    let sampleInterval = 1;
    let selectedWindow = 300;
    let historySamples = [];
    let inspectorGeneration = 0;
    let inspectorAbort = null;
    let focusBeforeModal = null;
    let focusBeforeInspector = null;
    let storagePoll = null;
    let storagePath = null;
    let storageLoadedAt = 0;
    let systemLoadedAt = 0;
    const RING_CIRC = 201.06;
    let processes = [];
    let processTree = [];
    let processView = "flat";
    let memoryUsedLabel = "—";
    let ws;
    let sessionDead = false;
    let lastRenderMs = null;
    let retryMs = 1000;
    let reconnectTimer = null;
    let productImageLoaded = false;
    let productImageObjectUrl = null;

    async function loadProductImage(available) {
      const img = $("host-product-image");
      const fallback = $("host-product-fallback");
      if (!img) return;
      if (!available) {
        img.classList.add("hidden");
        if (fallback) fallback.classList.remove("hidden");
        return;
      }
      if (productImageLoaded) return;
      productImageLoaded = true;
      try {
        const response = await fetch("/api/host/product-image");
        if (!response.ok) throw new Error("image unavailable");
        const blob = await response.blob();
        if (productImageObjectUrl) URL.revokeObjectURL(productImageObjectUrl);
        productImageObjectUrl = URL.createObjectURL(blob);
        img.src = productImageObjectUrl;
        img.alt = "This Mac";
        img.classList.remove("hidden");
        if (fallback) fallback.classList.add("hidden");
      } catch (_err) {
        productImageLoaded = false;
        img.classList.add("hidden");
        if (fallback) fallback.classList.remove("hidden");
      }
    }
    let sawDisconnect = false;
    let toastTimer = null;
    let pendingKill = null;
    let nqTimer = null;
    let socketUrl = `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws`;

    const MEM_COLORS = ["#6d28d9", "#8b5cf6", "#c4b5fd", "#475569", "#0ea5e9", "#14b8a6", "#64748b"];
    const MEM_LABELS = ["Wired", "Active", "Compressed", "Free", "Inactive", "Speculative", "Other"];
    const MEM_SWATCH = MEM_COLORS;

    Chart.defaults.font.family = '-apple-system, BlinkMacSystemFont, "SF Pro Text", sans-serif';
    Chart.defaults.font.size = 11;
    Chart.defaults.color = "#94a3b8";
    Chart.defaults.animation = false;
    Chart.defaults.responsive = true;
    Chart.defaults.maintainAspectRatio = false;

    const axisGrid = { color: "rgba(148,163,184,0.08)" };

    const cpuChart = new Chart($("cpu-chart"), {
      type: "line",
      data: {
        labels: [],
        datasets: [{
          label: "CPU",
          data: [],
          borderColor: "#3b82f6",
          backgroundColor: "rgba(59, 130, 246, 0.14)",
          fill: true,
          tension: 0.35,
          pointRadius: 0,
          borderWidth: 2,
        }],
      },
      options: {
        interaction: { mode: "index", intersect: false },
        plugins: { legend: { display: false } },
        scales: {
          x: { grid: { display: false }, ticks: { maxTicksLimit: 6, color: "#64748b" } },
          y: {
            min: 0,
            max: 100,
            grid: axisGrid,
            ticks: { callback: (value) => `${value}%`, color: "#64748b" },
          },
        },
      },
    });

    let chartInk = "#ffffff";
    let chartMuted = "#94a3b8";

    const gpuChart = new Chart($("gpu-chart"), {
      type:"line", data:{datasets:[{label:"GPU",data:[],borderColor:"#10b981",backgroundColor:"rgba(16,185,129,.12)",fill:true,pointRadius:0,borderWidth:2,spanGaps:false}]},
      options:{plugins:{legend:{display:false}},scales:{x:{type:"linear",grid:{display:false},ticks:{maxTicksLimit:6}},y:{min:0,max:100,grid:axisGrid,ticks:{callback:value=>`${value}%`}}}}
    });

    const centerText = {
      id: "centerText",
      afterDraw(chart) {
        const { ctx, chartArea } = chart;
        if (!chartArea) return;
        const x = (chartArea.left + chartArea.right) / 2;
        const y = (chartArea.top + chartArea.bottom) / 2;
        ctx.save();
        ctx.textAlign = "center";
        ctx.fillStyle = chartInk;
        ctx.font = '600 18px "SF Mono", ui-monospace, Menlo, monospace';
        ctx.fillText(memoryUsedLabel, x, y - 2);
        ctx.fillStyle = chartMuted;
        ctx.font = '11px -apple-system, BlinkMacSystemFont, "SF Pro Text", sans-serif';
        ctx.fillText("used", x, y + 14);
        ctx.restore();
      },
    };

    const memChart = new Chart($("mem-chart"), {
      type: "doughnut",
      data: {
        labels: MEM_LABELS,
        datasets: [{
          data: [0, 0, 0, 0],
          backgroundColor: MEM_COLORS,
          borderWidth: 0,
          spacing: 2,
          hoverOffset: 4,
        }],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        cutout: "68%",
        plugins: {
          legend: { display: false },
          tooltip: {
            callbacks: {
              label(context) {
                return ` ${context.label}: ${formatBytes(context.raw)}`;
              },
            },
          },
        },
      },
      plugins: [centerText],
    });

    const netChart = new Chart($("net-chart"), {
      type: "line",
      data: {
        labels: [],
        datasets: [
          {
            label: "Download",
            data: [],
            borderColor: "#10b981",
            backgroundColor: "rgba(16, 185, 129, 0.12)",
            fill: true,
            tension: 0.35,
            pointRadius: 0,
            borderWidth: 2,
          },
          {
            label: "Upload",
            data: [],
            borderColor: "#38bdf8",
            backgroundColor: "rgba(56, 189, 248, 0.10)",
            fill: true,
            tension: 0.35,
            pointRadius: 0,
            borderWidth: 2,
          },
        ],
      },
      options: {
        interaction: { mode: "index", intersect: false },
        plugins: {
          legend: { display: false },
          tooltip: {
            callbacks: {
              label(context) {
                return ` ${context.dataset.label}: ${formatRate(context.raw)}`;
              },
            },
          },
        },
        scales: {
          x: { grid: { display: false }, ticks: { maxTicksLimit: 6, color: "#64748b" } },
          y: {
            beginAtZero: true,
            grid: axisGrid,
            ticks: {
              maxTicksLimit: 5,
              color: "#64748b",
              callback: (value) => formatRate(value),
            },
          },
        },
      },
    });

    function refreshIcons() {
      if (window.lucide && typeof lucide.createIcons === "function") {
        lucide.createIcons();
      }
    }

    function currentTheme() {
      return document.documentElement.dataset.theme === "light" ? "light" : "dark";
    }

    function applyChartTheme(theme) {
      const light = theme === "light";
      chartInk = light ? "#0f172a" : "#ffffff";
      chartMuted = light ? "#64748b" : "#94a3b8";
      const grid = light ? "rgba(15, 23, 42, 0.08)" : "rgba(148, 163, 184, 0.08)";
      Chart.defaults.color = chartMuted;
      for (const chart of [cpuChart, netChart, gpuChart]) {
        chart.options.scales.x.ticks.color = "#64748b";
        chart.options.scales.y.ticks.color = "#64748b";
        chart.options.scales.y.grid.color = grid;
        chart.update("none");
      }
      memChart.update("none");
    }

    function syncThemeButton(theme) {
      const button = $("theme-toggle");
      if (!button) return;
      const light = theme === "light";
      button.setAttribute("aria-pressed", light ? "true" : "false");
      button.setAttribute("aria-label", light ? "Switch to dark mode" : "Switch to light mode");
      const icon = document.createElement("i");
      icon.setAttribute("data-lucide", light ? "moon" : "sun");
      icon.className = "h-4 w-4";
      button.replaceChildren(icon);
      refreshIcons();
    }

    function setTheme(theme) {
      const root = document.documentElement;
      root.dataset.theme = theme;
      root.style.colorScheme = theme;
      root.classList.toggle("dark", theme === "dark");
      const meta = document.querySelector('meta[name="color-scheme"]');
      if (meta) meta.content = theme;
      try { localStorage.setItem("sentinel-theme", theme); } catch (error) { /* private mode */ }
      syncThemeButton(theme);
      applyChartTheme(theme);
    }

    syncThemeButton(currentTheme());
    applyChartTheme(currentTheme());
    $("theme-toggle").addEventListener("click", () => {
      setTheme(currentTheme() === "light" ? "dark" : "light");
    });

    function sidebarCollapsed() {
      return document.body.classList.contains("sidebar-collapsed");
    }

    function setSidebarCollapsed(collapsed) {
      document.body.classList.toggle("sidebar-collapsed", collapsed);
      const button = $("sidebar-toggle");
      if (button) {
        button.setAttribute("aria-expanded", collapsed ? "false" : "true");
        button.setAttribute("aria-label", collapsed ? "Expand navigation" : "Collapse navigation");
        button.title = collapsed ? "Expand navigation" : "Collapse navigation";
        button.replaceChildren();
        const mark = document.createElement("span");
        mark.setAttribute("aria-hidden", "true");
        mark.textContent = collapsed ? ">" : "<>";
        button.append(mark);
      }
      try { localStorage.setItem("sentinel-sidebar", collapsed ? "collapsed" : "expanded"); } catch (_error) { /* private mode */ }
      // Charts need a resize pass after the main column width changes.
      window.setTimeout(() => window.dispatchEvent(new Event("resize")), 220);
    }

    try {
      setSidebarCollapsed(localStorage.getItem("sentinel-sidebar") === "collapsed");
    } catch (_error) {
      setSidebarCollapsed(false);
    }
    $("sidebar-toggle").addEventListener("click", () => {
      setSidebarCollapsed(!sidebarCollapsed());
    });

    function loadColor(value, base) {
      const n = Number(value) || 0;
      if (n >= 90) return "#ef4444";
      if (n >= 70) return "#f59e0b";
      return base;
    }

    function formatBytes(value) {
      if (value == null || Number.isNaN(Number(value))) return "—";
      const units = ["B", "KiB", "MiB", "GiB", "TiB"];
      let size = Number(value);
      let index = 0;
      while (size >= 1024 && index < units.length - 1) {
        size /= 1024;
        index += 1;
      }
      const digits = index === 0 ? 0 : size >= 100 ? 0 : size >= 10 ? 1 : 2;
      return `${size.toFixed(digits)} ${units[index]}`;
    }

    function formatRate(bytesPerSec) {
      if (bytesPerSec == null || Number.isNaN(Number(bytesPerSec))) return "—";
      return `${formatBytes(bytesPerSec)}/s`;
    }

    function formatPct(value, digits = 1) {
      if (value == null || Number.isNaN(Number(value))) return "—";
      return Number(value).toFixed(digits);
    }

    function setBar(id, percent) {
      const width = Math.max(0, Math.min(100, Number(percent) || 0));
      $(id).style.width = `${width}%`;
    }

    function stamp() {
      return new Date().toLocaleTimeString([], {
        hour12: false,
        hour: "2-digit",
        minute: "2-digit",
        second: "2-digit",
      });
    }

    function resetHistory() {
      [cpuChart, netChart, gpuChart].forEach((chart) => {
        chart.data.labels.length = 0;
        chart.data.datasets.forEach((dataset) => {
          dataset.data.length = 0;
        });
        chart.update("none");
      });
    }

    function setCpuStatus(cpu) {
      const chip = $("cpu-status");
      chip.className = "chip";
      if (cpu > 85) {
        chip.classList.add("border-red-500/30", "bg-red-500/10", "text-red-300");
        chip.textContent = "Critical";
      } else if (cpu >= 60) {
        chip.classList.add("border-amber-500/30", "bg-amber-500/10", "text-amber-300");
        chip.textContent = "Elevated";
      } else {
        chip.classList.add("border-blue-500/30", "bg-blue-500/10", "text-blue-300");
        chip.textContent = "Nominal";
      }
    }

    function updateAlerts(cpu, memPct) {
      $("cpu-tile").classList.toggle("alert-cpu", cpu > 85);
      $("cores-tile").classList.toggle("alert-cpu", cpu > 85);
      $("mem-tile").classList.toggle("alert-mem", memPct > 80);
    }

    function updateRing(id, pct, color) {
      const ring = $(id);
      if (!ring) return;
      const value = Math.max(0, Math.min(100, Number(pct) || 0));
      ring.style.strokeDashoffset = String(RING_CIRC * (1 - value / 100));
      if (color) ring.setAttribute("stroke", color);
    }

    function updateCpuRing(cpu) {
      const pct = Math.max(0, Math.min(100, Number(cpu) || 0));
      updateRing("cpu-ring", pct, loadColor(pct, "#3b82f6"));
    }

    function setConnection(connected) {
      const pill = $("conn-pill");
      const dot = $("conn-dot");
      const label = $("conn-label");
      $("conn-url").textContent = socketUrl;
      if (connected) {
        pill.className = "chip border-emerald-500/40 bg-emerald-500/10 text-emerald-300";
        dot.className = "live-dot bg-emerald-400";
        label.textContent = "Connected";
      } else {
        pill.className = "chip border-red-500/70 bg-red-500/10 text-red-300";
        dot.className = "h-1.5 w-1.5 rounded-full bg-red-500";
        label.textContent = "Disconnected";
      }
    }

    function showSessionExpired() {
      sessionDead = true;
      if (reconnectTimer) {
        clearTimeout(reconnectTimer);
        reconnectTimer = null;
      }
      const pill = $("conn-pill");
      const dot = $("conn-dot");
      pill.className = "chip border-amber-500/30 bg-amber-500/10 text-amber-300";
      dot.className = "h-1.5 w-1.5 rounded-full bg-amber-400";
      $("conn-label").textContent = "Reload page";
      $('conn-pill').title='Click to reload and reconnect to the new server session';
      $('conn-pill').onclick=()=>location.reload();
      $('conn-pill').setAttribute('aria-label','Server restarted. Click to reload dashboard');
      showToast("Server restarted. Click Reload page to reconnect; browser watch names are retained.");
    }

    async function sessionRejected() {
      try {
        const response = await fetch("/api/history?seconds=30");
        return response.status === 403;
      } catch (error) {
        return false;
      }
    }

    function connect() {
      if (sessionDead) return;
      if (reconnectTimer) {
        clearTimeout(reconnectTimer);
        reconnectTimer = null;
      }
      const protocol = location.protocol === "https:" ? "wss" : "ws";
      socketUrl = `${protocol}://${location.host}/ws`;
      let opened = false;
      ws = socket(socketUrl);
      ws.onopen = () => {
        opened = true;
        setConnection(true);
        retryMs = 1000;
        loadHistory();
        sawDisconnect = false;
      };
      ws.onmessage = (event) => {
        try {
          const renderStarted=performance.now();
          applyTelemetry(JSON.parse(event.data));
          lastRenderMs=performance.now()-renderStarted;
        } catch (error) {
          console.error(error);
        }
      };
      ws.onerror = () => setConnection(false);
      ws.onclose = async () => {
        setConnection(false);
        if (sessionDead) return;
        // A rejected handshake is HTTP 403, which the browser reports as a
        // failed socket. That means this page's per-run token is no longer
        // valid, so retrying only fills the server log.
        if (!opened && await sessionRejected()) {
          showSessionExpired();
          return;
        }
        sawDisconnect = true;
        reconnectTimer = setTimeout(connect, retryMs);
        retryMs = Math.min(Math.round(retryMs * 1.5), 8000);
      };
    }

    function applyTelemetry(data) {
      const label = new Date(data.timestamp).toLocaleTimeString();
      lastSampleAt = Date.parse(data.timestamp);
      sampleInterval = data.sample_interval || 1;
      $("sample-interval").value = String(data.requested_interval || sampleInterval);
      $("adaptive-sampling").checked=Boolean(data.adaptive_sampling);
      if(document.hidden) {recordHistory(data);insightSample=data;return;}
      recordHistory(data);
      renderDiagnostics(data);
      const host = data.host || {};
      const topology = data.topology || {};
      $("host-model").textContent = host.chip || host.model || "Unknown Mac";
      $("host-name").textContent = host.hostname || "localhost";
      const osLabel = host.os_build && host.os_version
        ? `${host.os_product || "macOS"} ${host.os_version} (${host.os_build})`
        : (host.os || "macOS");
      $("host-os").textContent = osLabel;
      $("about-machine-name").textContent = host.machine_name || "Mac";
      $("about-year").textContent = host.marketing_year || "";
      $("about-memory").textContent = host.memory || "—";
      $("about-startup-disk").textContent = host.startup_disk || "—";
      $("about-serial").textContent = host.serial_number || "—";
      $("about-arch").textContent = host.architecture || "—";
      loadProductImage(Boolean(host.product_image_available));
      $("uptime").textContent = formatUptime(host.uptime_seconds);

      const pCores = topology.performance_cores || 0;
      const eCores = topology.efficiency_cores || 0;
      const coreCount = host.cpu_count || (data.cpu.per_core || []).length;
      if (pCores || eCores) {
        $("cpu-topology").textContent = `${coreCount} cores · ${pCores}P + ${eCores}E`;
      } else {
        $("cpu-topology").textContent = `${coreCount} cores`;
      }

      const cpu = data.cpu.percent;
      $("cpu-pct").textContent = formatPct(cpu);
      $("cpu-live").textContent = `${formatPct(cpu)}%`;
      $("cpu-sub").textContent = pCores || eCores
        ? `${pCores} performance · ${eCores} efficiency`
        : `${(data.cpu.per_core || []).length} logical cores`;
      updateCpuRing(cpu);
      setCpuStatus(cpu);


      const memory = data.memory;
      memoryUsedLabel = `${formatPct(memory.percent, 0)}%`;
      $("mem-pct").textContent = formatPct(memory.percent);
      $("mem-sub").textContent = `${formatBytes(memory.used)} / ${formatBytes(memory.total)}`;
      $("mem-sub").title = "Used estimate = total minus available estimate; includes compressed memory. Not an exact Activity Monitor accounting.";
      $("mem-free").textContent = `${formatBytes(memory.free)} free · ${formatBytes(memory.available)} available estimate`;
      const slices = [memory.wired, memory.active, memory.compressed, memory.vm_free, memory.inactive, memory.speculative, memory.other];
      memChart.data.datasets[0].data = slices;
      memChart.update("none");
      renderMemoryLegend(slices);
      $("mem-chart").title=memory.breakdown_status === "partial" ? "Partial breakdown: vm_stat unavailable" : "Physical memory categories; OS samples may be slightly asynchronous";
      updateAlerts(cpu, memory.percent);

      if (currentTab === "overview") renderCores(data.cpu.per_core, 0, 0);

      const network = data.network;
      $("net-down").textContent = formatRate(network.download_bps);
      $("net-up").textContent = formatRate(network.upload_bps);


      const disk = data.disk;
      $("disk-mount").textContent = "Startup storage";
      $("disk-pct").textContent = formatPct(disk.percent);
      $("disk-sub").textContent = disk.status === "unavailable" ? "Disk data unavailable" : `${formatStorage(disk.used)} used of ${formatStorage(disk.total)}`;
      $("disk-free").textContent = disk.available == null ? `${formatStorage(disk.free)} physically free · available estimate unavailable` : `${formatStorage(disk.available)} available including reclaimable`;
      $("disk-reclaimable").textContent = disk.available == null ? "Using physical allocation for used %" : `${formatStorage(disk.free)} physically free · ${formatStorage(disk.reclaimable)} reclaimable headroom`;
      $("disk-sub").title = `Used = total − ${disk.available == null ? "physically free" : "macOS available capacity"}. Updated ${disk.checked_at || "with this sample"}`;
      updateRing("disk-ring", disk.percent, loadColor(disk.percent, "#8b5cf6"));

      renderBattery(data.battery);
      processes = data.processes || [];
      processTree = data.process_tree || processes;
      if (currentTab === "processes" && $("modal").classList.contains("hidden")) renderProcesses();
    }

    function renderMemoryLegend(slices) {
      const list = $("mem-legend");
      list.replaceChildren();
      slices.forEach((value, index) => {
        const item = document.createElement("li");
        item.className = "flex items-center justify-between gap-3";
        const left = document.createElement("span");
        left.className = "flex items-center gap-2 text-slate-300";
        const swatch = document.createElement("span");
        swatch.className = "h-2 w-2 rounded-sm";
        swatch.style.backgroundColor = MEM_SWATCH[index];
        left.append(swatch, document.createTextNode(MEM_LABELS[index]));
        const right = document.createElement("span");
        right.className = "metric text-xs text-slate-400";
        right.textContent = formatBytes(value);
        item.append(left, right);
        list.appendChild(item);
      });
    }

    function renderCores(perCore, pCount, eCount) {
      const values = perCore || [];
      if (pCount === 0 && eCount === 0) {
        $("p-label").textContent = "Cores";
        $("core-caption").textContent = "Logical core indices · P/E index mapping unavailable";
        $("e-section").classList.add("hidden");
        $("p-count").textContent = String(values.length);
        fillCoreGrid($("p-grid"), values, "C", "#3b82f6");
        return;
      }
      $("p-label").textContent = "Performance";
      $("e-label").textContent = "Efficiency";
      $("core-caption").textContent = "Grouped from hw.perflevel physical cores";
      $("e-section").classList.toggle("hidden", eCount === 0);
      $("p-count").textContent = String(pCount);
      $("e-count").textContent = String(eCount);
      fillCoreGrid($("p-grid"), values.slice(0, pCount), "P", "#3b82f6");
      fillCoreGrid($("e-grid"), values.slice(pCount, pCount + eCount), "E", "#60a5fa");
    }

    function fillCoreGrid(grid, values, prefix, baseColor) {
      grid.replaceChildren();
      values.forEach((value, index) => {
        const cell = document.createElement("div");
        cell.className = "rounded-xl border border-slate-800 bg-slate-950/40 px-2 py-1.5";
        const row = document.createElement("div");
        row.className = "flex items-center justify-between text-[10px]";
        const name = document.createElement("span");
        name.className = "text-slate-500";
        name.textContent = `${prefix}${index + 1}`;
        const pct = document.createElement("span");
        pct.className = "metric text-slate-100";
        pct.textContent = `${Math.round(value)}%`;
        row.append(name, pct);
        const track = document.createElement("div");
        track.className = "bar-track mt-1 h-1";
        const bar = document.createElement("div");
        bar.className = "bar-fill";
        bar.style.width = `${Math.max(0, Math.min(100, value))}%`;
        bar.style.background = loadColor(value, baseColor);
        track.appendChild(bar);
        cell.append(row, track);
        grid.appendChild(cell);
      });
    }

    function renderBattery(battery) {
      const source = battery.source || "Unknown";
      $("batt-source").textContent = source;
      const chip = $("batt-chip");
      const unit = $("batt-unit");
      const details = $("batt-details");
      details.replaceChildren();

      function addDetail(label, value) {
        if (value == null || value === "") return;
        const row = document.createElement("div");
        row.className = "flex items-baseline justify-between gap-3";
        const dt = document.createElement("dt");
        dt.className = "text-slate-500";
        dt.textContent = label;
        const dd = document.createElement("dd");
        dd.className = "metric text-slate-300";
        dd.textContent = value;
        row.append(dt, dd);
        details.appendChild(row);
      }

      if (battery.collection_status === "unavailable") {
        $("batt-pct").textContent = "—";
        unit.textContent = "";
        $("batt-status").textContent = "Battery data unavailable";
        $("batt-time").textContent = "";
        chip.textContent = "Unknown";
        updateRing("batt-ring", 0, "#64748b");
        return;
      }
      if (!battery.present) {
        // Desktop / AC-only: full green ring with AC in the center
        $("batt-pct").textContent = "AC";
        unit.textContent = "";
        $("batt-status").textContent = "Mains power";
        $("batt-time").textContent = `Drawing from ${source}`;
        chip.className = "chip border-emerald-500/30 bg-emerald-500/10 text-emerald-300";
        chip.textContent = "AC";
        updateRing("batt-ring", 100, "#10b981");
        addDetail("Internal battery", "None");
        addDetail("Power source", source);
        if (battery.system_power_w != null) addDetail("System draw", `${Number(battery.system_power_w).toFixed(1)} W`);
        if (battery.adapter_watts) addDetail("Adapter", `${battery.adapter_watts} W`);
        if (battery.system_voltage_mv != null) addDetail("Input voltage", `${(battery.system_voltage_mv / 1000).toFixed(2)} V`);
        if (battery.system_current_ma != null && battery.system_current_ma !== 0) {
          addDetail("Input current", `${battery.system_current_ma} mA`);
        }
        if (battery.ups_installed === true) addDetail("UPS", "Installed");
        else if (battery.ups_installed === false) addDetail("UPS", "Not installed");
        if (battery.wake_on_lan === true) addDetail("Wake on LAN", "On");
        else if (battery.wake_on_lan === false) addDetail("Wake on LAN", "Off");
        if (battery.auto_restart_on_power_loss === true) addDetail("Restart on power loss", "On");
        else if (battery.auto_restart_on_power_loss === false) addDetail("Restart on power loss", "Off");
        if (battery.display_sleep_minutes != null) {
          addDetail("Display sleep", battery.display_sleep_minutes === 0 ? "Never" : `${battery.display_sleep_minutes} min`);
        }
        if (battery.disk_sleep_minutes != null) {
          addDetail("Disk sleep", battery.disk_sleep_minutes === 0 ? "Never" : `${battery.disk_sleep_minutes} min`);
        }
        return;
      }
      const level = Number.isFinite(battery.percent) ? battery.percent : 0;
      $("batt-pct").textContent = battery.percent == null ? "—" : formatPct(battery.percent, 0);
      unit.textContent = battery.percent == null ? "" : "%";
      const status = battery.status || "unknown";
      const pretty = status.replace(/\b\w/g, (letter) => letter.toUpperCase());
      const charging = battery.charging || status === "charging" || status === "finishing charge";
      $("batt-status").textContent = charging ? `${pretty} · AC` : pretty;
      if (battery.time_remaining && battery.time_remaining !== "no estimate") {
        const label = charging ? "until full" : "remaining";
        $("batt-time").textContent = status === "charged" ? "Fully charged" : `${battery.time_remaining} ${label}`;
      } else if (status === "charged") {
        $("batt-time").textContent = "Fully charged";
      } else if (battery.time_remaining === "no estimate") {
        $("batt-time").textContent = "No time estimate";
      } else {
        $("batt-time").textContent = source;
      }
      let color = "#10b981";
      if (level < 20 && !charging) {
        chip.className = "chip border-red-500/30 bg-red-500/10 text-red-300";
        chip.textContent = "Low";
        color = "#ef4444";
      } else if (charging) {
        chip.className = "chip border-blue-500/30 bg-blue-500/10 text-blue-300";
        chip.textContent = "Charging";
        color = "#3b82f6";
      } else if (battery.power_state === "Battery" || status === "discharging") {
        chip.className = "chip border-amber-500/30 bg-amber-500/10 text-amber-300";
        chip.textContent = "Battery";
        color = "#f59e0b";
      } else {
        chip.className = "chip border-emerald-500/30 bg-emerald-500/10 text-emerald-300";
        chip.textContent = "AC";
        color = "#10b981";
      }
      updateRing("batt-ring", level, color);
      addDetail("Power source", source);
      if (battery.system_power_w != null) addDetail("System draw", `${Number(battery.system_power_w).toFixed(1)} W`);
      if (battery.cycle_count != null) addDetail("Cycle count", String(battery.cycle_count));
      if (battery.condition) addDetail("Condition", battery.condition);
      else if (battery.health) addDetail("Health", battery.health);
      if (battery.max_capacity_percent != null) addDetail("Max capacity", `${battery.max_capacity_percent}%`);
      if (battery.current_capacity_mah != null && battery.max_capacity_mah != null) {
        addDetail("Charge", `${battery.current_capacity_mah} / ${battery.max_capacity_mah} mAh`);
      } else if (battery.design_capacity_mah != null) {
        addDetail("Design capacity", `${battery.design_capacity_mah} mAh`);
      }
      if (battery.voltage_mv != null) addDetail("Voltage", `${(battery.voltage_mv / 1000).toFixed(2)} V`);
      if (battery.amperage_ma != null && battery.amperage_ma !== 0) {
        addDetail("Current", `${battery.amperage_ma} mA`);
      }
      if (battery.temperature_c != null) addDetail("Temperature", `${battery.temperature_c} °C`);
      if (battery.adapter_watts) addDetail("Adapter", `${battery.adapter_watts} W`);
      if (battery.ups_installed === true) addDetail("UPS", "Installed");
      if (battery.wake_on_lan === true) addDetail("Wake on LAN", "On");
      else if (battery.wake_on_lan === false) addDetail("Wake on LAN", "Off");
    }

    function formatUptime(seconds) {
      const total = Math.max(0, Math.floor(Number(seconds) || 0));
      const days = Math.floor(total / 86400);
      const hours = Math.floor((total % 86400) / 3600);
      const minutes = Math.floor((total % 3600) / 60);
      const secs = total % 60;
      const clock = [hours, minutes, secs].map((part) => String(part).padStart(2, "0")).join(":");
      return days ? `${days}d ${clock}` : clock;
    }

    function visibleProcesses() {
      const query = $("proc-filter").value.trim().toLowerCase();
      if (processView !== "tree") {
        return processes
          .filter((proc) => !query || `${proc.name} ${proc.application || ""}`.toLowerCase().includes(query))
          .map((proc) => ({ ...proc, depth: 0 }));
      }
      const byPid = new Map(processTree.map((proc) => [proc.pid, proc]));
      const children = new Map();
      processTree.forEach((proc) => {
        const parent = byPid.has(proc.ppid) ? proc.ppid : 0;
        if (!children.has(parent)) children.set(parent, []);
        children.get(parent).push(proc);
      });
      children.forEach((list) => list.sort((a, b) => b.cpu_percent - a.cpu_percent || a.name.localeCompare(b.name)));
      const ordered = [];
      const visited = new Set();
      const walk = (pid, depth) => {
        (children.get(pid) || []).forEach((proc) => {
          if (visited.has(proc.pid)) return;
          visited.add(proc.pid);
          ordered.push({ ...proc, depth });
          walk(proc.pid, depth + 1);
        });
      };
      walk(0, 0);
      processTree.forEach(proc=>{if(!visited.has(proc.pid)) ordered.push({...proc,depth:0});});
      if (!query) return ordered;
      const keep = new Set();
      ordered.forEach((proc) => {
        if (!`${proc.name} ${proc.application || ""}`.toLowerCase().includes(query)) return;
        keep.add(proc.pid);
        let parent = proc.ppid;
        let guard = 0;
        while (parent && byPid.has(parent) && guard < 16) {
          keep.add(parent);
          parent = byPid.get(parent).ppid;
          guard += 1;
        }
      });
      return ordered.filter((proc) => keep.has(proc.pid));
    }

    function renderProcesses() {
      const query = $("proc-filter").value.trim().toLowerCase();
      const sort = $("proc-sort").value;
      let rows = visibleProcesses();
      if (processView !== "tree") rows.sort((a,b) => sort === "name" ? a.name.localeCompare(b.name) : b[sort]-a[sort]);
      if ($("proc-group").checked) {
        rows = SentinelCore.groupProcesses(rows, sort);
      }
      const focusedPid = document.activeElement?.dataset?.pid;
      const focusedAction = document.activeElement?.dataset?.action;
      const body = $("proc-body");
      body.replaceChildren();
      const sourceCount = processView === "tree" ? processTree.length : processes.length;
      $("proc-count").textContent = processView === "tree"
        ? (query ? `${rows.length} in the filtered tree` : `Tree · ${sourceCount} processes including parents`)
        : (query ? `${rows.length} of ${processes.length} shown · all sampled processes` : `${processes.length} processes · CPU uses 100% per logical core`);
      if (!rows.length) {
        $("proc-page").textContent="No matches";$("proc-prev").disabled=true;$("proc-next").disabled=true;
        const tr = document.createElement("tr");
        const td = document.createElement("td");
        td.colSpan = 5;
        td.className = "px-2 py-8 text-center text-sm text-slate-500";
        td.textContent = processes.length
          ? "No processes match that filter."
          : "Waiting for the first process sample…";
        tr.appendChild(td);
        body.appendChild(tr);
        return;
      }
      processPage = Math.min(processPage,Math.max(0,Math.ceil(rows.length/100)-1));
      $("proc-page").textContent = `Page ${processPage+1} / ${Math.max(1,Math.ceil(rows.length/100))} · ${rows.length} matches`;
      $("proc-prev").disabled = processPage===0;
      $("proc-next").disabled = (processPage+1)*100>=rows.length;
      rows.slice(processPage*100,(processPage+1)*100).forEach((proc) => {
        const tr = document.createElement("tr");
        tr.className = "cursor-pointer border-b border-slate-800/60 last:border-0 hover:bg-slate-800/40";
        tr.dataset.pid = String(proc.pid);
        tr.dataset.name = proc.name;
        tr.dataset.application = proc.application || proc.name;
        tr.dataset.grouped = proc.grouped ? "true" : "false";
        tr.tabIndex = 0;
        tr.setAttribute("aria-label", `Inspect ${proc.name}`);
        if (proc.cpu_percent >= 90) tr.classList.add("hot");
        const pid = document.createElement("td");
        pid.className = "metric px-2 py-2.5 text-slate-400";
        pid.textContent = String(proc.pid);
        const name = document.createElement("td");
        name.className = "max-w-[280px] truncate px-2 py-2.5 text-slate-100";
        name.style.paddingLeft = `${8 + (proc.depth || 0) * 16}px`;
        name.textContent = proc.depth ? `↳ ${proc.name}` : proc.name;
        name.title = proc.name;
        const cpu = document.createElement("td");
        cpu.className = "px-2 py-2.5";
        const cpuWrap = document.createElement("div");
        cpuWrap.className = "flex items-center justify-end gap-2";
        const cpuTrack = document.createElement("div");
        cpuTrack.className = "bar-track hidden h-1 w-16 sm:block";
        const cpuBar = document.createElement("div");
        cpuBar.className = "bar-fill";
        cpuBar.style.width = `${Math.max(0, Math.min(100, proc.cpu_percent))}%`;
        cpuBar.style.background = loadColor(proc.cpu_percent, "#3b82f6");
        cpuTrack.appendChild(cpuBar);
        const cpuText = document.createElement("span");
        cpuText.className = "metric w-14 text-right text-slate-200";
        cpuText.textContent = `${formatPct(proc.cpu_percent)}%`;
        cpuWrap.append(cpuTrack, cpuText);
        cpu.appendChild(cpuWrap);
        const memory = document.createElement("td");
        memory.className = "metric px-2 py-2.5 text-right text-slate-300";
        memory.textContent = `${formatPct(proc.memory_percent)}%`;
        const action = document.createElement("td");
        action.className = "px-2 py-2.5 text-right";
        const button = document.createElement("button");
        button.type = "button";
        button.className = "btn-danger px-2.5 py-1 text-xs";
        button.textContent = "Kill";
        button.dataset.pid = String(proc.pid);
        button.dataset.name = proc.name;
        button.dataset.created = String(proc.create_time);
        button.dataset.action = "terminate";
        button.disabled = Boolean(proc.grouped);
        button.setAttribute("aria-label", `Kill ${proc.name}`);
        action.appendChild(button);
        tr.append(pid, name, cpu, memory, action);
        body.appendChild(tr);
      });
      if (focusedPid) {
        const target = Array.from(body.querySelectorAll(focusedAction ? "button[data-pid]" : "tr[data-pid]")).find(el => el.dataset.pid === focusedPid);
        target?.focus({preventScroll:true});
      }
    }

    function openModal(pid, name, create_time) {
      pendingKill = { pid, name, create_time: Number(create_time) };
      focusBeforeModal = document.activeElement;
      $("modal-name").textContent = name;
      $("modal-pid").textContent = pid;
      $("modal-error").classList.add("hidden");
      $("modal-error").textContent = "";
      $("modal-confirm").disabled = false;
      $("modal-confirm").textContent = "End Process";
      const modal = $("modal");
      modal.classList.remove("hidden");
      modal.classList.add("flex");
      document.body.classList.add("overflow-hidden");
      $("modal-cancel").focus();
    }

    function closeModal() {
      pendingKill = null;
      const modal = $("modal");
      modal.classList.add("hidden");
      modal.classList.remove("flex");
      document.body.classList.remove("overflow-hidden");
      if (focusBeforeModal?.isConnected) focusBeforeModal.focus();
      else $("proc-filter").focus();
    }

    async function confirmKill() {
      if (!pendingKill) return;
      const { pid, name, create_time } = pendingKill;
      const button = $("modal-confirm");
      button.disabled = true;
      button.textContent = "Sending…";
      $("modal-error").classList.add("hidden");
      try {
        const response = await fetch(`/api/process/kill/${pid}`, { method: "POST", headers: {"Content-Type":"application/json"}, body:JSON.stringify({create_time}) });
        const body = await response.json().catch(() => ({}));
        if (!response.ok) {
          throw new Error(errorDetail(body, response.statusText));
        }
        showToast(`SIGTERM sent to ${body.name || name} (${body.pid || pid})`);
        closeModal();
      } catch (error) {
        $("modal-error").textContent = error.message || "Could not signal that process.";
        $("modal-error").classList.remove("hidden");
        button.disabled = false;
        button.textContent = "End Process";
      }
    }

    function errorDetail(body, fallback) {
      if (!body) return fallback || "Request failed";
      if (typeof body.detail === "string") return body.detail;
      if (Array.isArray(body.detail)) {
        return body.detail.map((item) => item.msg || JSON.stringify(item)).join("; ");
      }
      return fallback || "Request failed";
    }

    function showToast(message) {
      const toast = $("toast");
      toast.textContent = message;
      toast.classList.remove("hidden");
      if (toastTimer) clearTimeout(toastTimer);
      toastTimer = setTimeout(() => toast.classList.add("hidden"), 3200);
    }

    function gradeClass(label) {
      if (label === "High") return "chip border-emerald-500/30 bg-emerald-500/10 text-emerald-300";
      if (label === "Medium") return "chip border-amber-500/30 bg-amber-500/10 text-amber-300";
      if (label === "Low") return "chip border-red-500/30 bg-red-500/10 text-red-300";
      return "chip border-slate-700 bg-slate-800/70 text-slate-300";
    }

    function jitterCaption(source) {
      if (!source) return "This run did not include latency samples.";
      if (source.startsWith("il_")) return `Mean absolute deviation of idle samples (${source}).`;
      return `Mean absolute deviation of latency-under-load samples (${source}).`;
    }

    async function runNetworkQuality() {
      const button = $("nq-btn");
      const spinner = $("nq-spinner");
      const label = $("nq-btn-label");
      const elapsed = $("nq-elapsed");
      button.disabled = true;
      spinner.classList.remove("hidden");
      label.textContent = "Testing…";
      elapsed.classList.remove("hidden");
      $("nq-error").classList.add("hidden");
      const started = Date.now();
      elapsed.textContent = "Running networkQuality… 0s";
      nqTimer = setInterval(() => {
        elapsed.textContent = `Running networkQuality… ${Math.floor((Date.now() - started) / 1000)}s`;
      }, 250);
      try {
        const response = await fetch("/api/network-quality");
        const body = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(errorDetail(body, "Speed test failed"));
        $("nq-result").classList.remove("hidden");
        $("nq-down").textContent = body.downlink_mbps == null ? "—" : `${body.downlink_mbps.toFixed(1)} Mbps`;
        $("nq-up").textContent = body.uplink_mbps == null ? "—" : `${body.uplink_mbps.toFixed(1)} Mbps`;
        $("nq-rpm").textContent = body.responsiveness_rpm == null
          ? "—"
          : `${Math.round(body.responsiveness_rpm).toLocaleString()} RPM`;
        const grade = $("nq-grade");
        grade.textContent = body.responsiveness_label || "Unknown";
        grade.className = gradeClass(body.responsiveness_label);
        $("nq-latency").textContent = body.latency_ms == null ? "—" : `${body.latency_ms.toFixed(1)} ms`;
        $("nq-jitter").textContent = body.jitter_ms == null ? "—" : `${body.jitter_ms.toFixed(1)} ms`;
        $("nq-jitter").title = jitterCaption(body.jitter_source);
        $("nq-iface").textContent = body.interface || "—";
        $("nq-duration").textContent = body.duration_s == null ? "—" : `${body.duration_s.toFixed(0)}s`;
        elapsed.textContent = "Latest test finished.";
      } catch (error) {
        $("nq-error").textContent = error.message || "Speed test failed.";
        $("nq-error").classList.remove("hidden");
        elapsed.classList.add("hidden");
      } finally {
        if (nqTimer) clearInterval(nqTimer);
        button.disabled = false;
        spinner.classList.add("hidden");
        label.textContent = "Network test";
      }
    }

    function renderSoftware(body) {
      const status = $("su-status");
      const list = $("su-list");
      list.replaceChildren();
      status.className = "text-xs";
      if (!body.ok && body.ok !== undefined && !body.pending && !(body.updates || []).length) {
        status.className += " text-red-300";
        status.textContent = body.summary || "Could not check for updates.";
      } else if (body.pending) {
        status.className += " font-medium text-amber-300";
        status.textContent = body.summary || "Updates available";
      } else {
        status.className += " font-medium text-emerald-300";
        status.textContent = body.summary || "No new software available.";
      }
      (body.updates || []).forEach((update) => {
        const item = document.createElement("li");
        item.className = "rounded-xl border border-slate-800 bg-slate-950/40 px-3 py-2";
        const title = document.createElement("p");
        title.className = "text-sm text-slate-100";
        title.textContent = update.title || update.label;
        const meta = document.createElement("p");
        meta.className = "mt-0.5 text-xs text-slate-500";
        const bits = [];
        if (update.version) bits.push(`Version ${update.version}`);
        if (update.size) bits.push(update.size);
        if (update.recommended) bits.push("Recommended");
        if (update.action) bits.push(update.action);
        meta.textContent = bits.join(" · ") || update.label;
        item.append(title, meta);
        list.appendChild(item);
      });
      if (body.checked_at) {
        const when = new Date(body.checked_at);
        $("su-checked").textContent = `Checked ${when.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })}`;
      }
    }

    async function loadSoftwareUpdates() {
      const button = $("su-btn");
      button.disabled = true;
      $("su-status").className = "flex items-center gap-2 text-xs text-slate-300";
      $("su-status").textContent = "";
      const spinner = document.createElement("span");
      spinner.className = "spinner";
      const text = document.createElement("span");
      text.textContent = "Checking for updates…";
      $("su-status").append(spinner, text);
      $("su-list").replaceChildren();
      try {
        const response = await fetch("/api/software-updates");
        const body = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(errorDetail(body, "Update check failed"));
        renderSoftware(body);
      } catch (error) {
        $("su-status").className = "text-xs text-red-300";
        $("su-status").textContent = error.message || "Update check failed.";
        $("su-checked").textContent = "";
      } finally {
        button.disabled = false;
      }
    }

    const NAV_IDLE = "nav-btn flex w-full items-center gap-3 border-r-2 border-transparent px-4 py-2.5 text-left text-sm font-medium text-slate-300 hover:bg-slate-800/60";
    const NAV_ACTIVE = "nav-btn flex w-full items-center gap-3 border-r-2 border-blue-500 bg-blue-600/20 px-4 py-2.5 text-left text-sm font-medium text-blue-400";
    const LOG_LIMIT = 2000;
    let logSocket = null;
    let logEntries = [];
    let logPaused = false;
    let logAutoScroll = true;
    let logStopRequested = false;
    let logRenderQueued = false;
    let storagePromise = null;
    let systemInfo = null;
    let systemPromise = null;
    let systemSection = "hardware";
    let installedApps = null;
    let installedAppsPromise = null;
    let installedAppsLoadedAt = 0;

    function showTab(name) {
      currentTab = name;
      if (name === "processes") renderProcesses();
      if (name === "overview") renderHistory();
      ["overview", "processes", "logs", "storage", "applications", "system", "settings"].forEach((tab) => {
        $(`view-${tab}`).classList.toggle("hidden", tab !== name);
      });
      document.querySelectorAll("[data-tab]").forEach((button) => {
        button.className = button.dataset.tab === name ? NAV_ACTIVE : NAV_IDLE;
        if (button.dataset.tab === name) button.setAttribute("aria-current", "page");
        else button.removeAttribute("aria-current");
      });
      if (name === "logs") startLogs();
      else stopLogs();
      if (name === "storage") loadStorage(false);
      if (name === "applications") loadInstalledApps(false);
      if (name === "system") loadSystemInfo(false);
      refreshIcons();
    }

    function setProcessView(mode) {
      processView = mode;
      $("proc-flat").className = mode === "flat"
        ? "rounded-md bg-blue-600/20 px-3 py-1.5 text-xs font-medium text-blue-300"
        : "rounded-md px-3 py-1.5 text-xs font-medium text-slate-400";
      $("proc-tree").className = mode === "tree"
        ? "rounded-md bg-blue-600/20 px-3 py-1.5 text-xs font-medium text-blue-300"
        : "rounded-md px-3 py-1.5 text-xs font-medium text-slate-400";
      renderProcesses();
    }

    function closeInspector() {
      inspectorGeneration++;
      inspectorAbort?.abort();
      const drawer = $("inspector");
      drawer.classList.add("hidden", "translate-x-full");
      drawer.classList.remove("flex");
      if(focusBeforeInspector?.isConnected) focusBeforeInspector.focus();
      else $("proc-filter").focus();
    }

    async function openInspector(pid, name) {
      focusBeforeInspector = document.activeElement;
      const generation = ++inspectorGeneration;
      inspectorAbort?.abort();
      inspectorAbort = new AbortController();
      const drawer = $("inspector");
      drawer.classList.remove("hidden", "translate-x-full");
      drawer.classList.add("flex");
      $("inspector-close").focus();
      $("inspector-title").textContent = name || `PID ${pid}`;
      $("inspector-meta").textContent = "Reading open files…";
      $("inspector-error").classList.add("hidden");
      $("inspector-files").textContent = "";
      $("inspector-sockets").textContent = "";
      try {
        const response = await fetch(`/api/process/${pid}/details`, {signal:inspectorAbort.signal});
        const body = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(errorDetail(body, "Could not inspect that process"));
        if (generation !== inspectorGeneration) return;
        const proc = body.process || {};
        const bits = [`PID ${proc.pid || pid}`];
        if (proc.username) bits.push(proc.username);
        if (proc.status) bits.push(proc.status);
        if (proc.threads != null) bits.push(`${proc.threads} threads`);
        $("inspector-title").textContent = proc.name || name || `PID ${pid}`;
        $("inspector-meta").textContent = bits.join(" · ");
        $("inspector-files").textContent = ((body.files || []).join("\n") || "No open files reported.") + (body.files_truncated ? "\n…truncated to 250 entries" : "");
        $("inspector-sockets").textContent = (body.sockets || []).join("\n") || "No sockets reported.";
        if (body.lsof_error) {
          $("inspector-error").textContent = body.lsof_error;
          $("inspector-error").classList.remove("hidden");
        }
      } catch (error) {
        if (generation !== inspectorGeneration || error.name === "AbortError") return;
        $("inspector-error").textContent = error.message || "Could not inspect that process.";
        $("inspector-error").classList.remove("hidden");
        $("inspector-meta").textContent = `PID ${pid}`;
      }
    }

    function logLevelClass(level) {
      const name = String(level || "").toLowerCase();
      if (name === "fault" || name === "error") return "text-red-300";
      if (name === "debug") return "text-slate-500";
      if (name === "info") return "text-sky-300";
      return "text-slate-200";
    }

    function logRecord(entry) {
      const path = entry.processImagePath || entry.process || "";
      const process = path ? String(path).split("/").pop() : "";
      return {
        level: entry.messageType || entry.level || "Default",
        subsystem: entry.subsystem || "",
        process,
        category: entry.category || "",
        message: entry.eventMessage || entry.message || "",
        time: entry.timestamp || "",
      };
    }

    function renderLogs() {
      const view = $("log-view");
      const level = $("log-level").value.toLowerCase();
      const subsystem = $("log-subsystem").value.trim().toLowerCase();
      const process = $("log-process").value.trim().toLowerCase();
      const category = $("log-category").value.trim().toLowerCase();
      const search = $("log-search").value.trim().toLowerCase();
      const rows = logEntries.filter((entry) => {
        if (level && String(entry.level).toLowerCase() !== level) return false;
        if (subsystem && !entry.subsystem.toLowerCase().includes(subsystem)) return false;
        if (process && !entry.process.toLowerCase().includes(process)) return false;
        if (category && !entry.category.toLowerCase().includes(category)) return false;
        if (search && !`${entry.message} ${entry.subsystem} ${entry.process}`.toLowerCase().includes(search)) return false;
        return true;
      });
      const atBottom = view.scrollHeight - view.scrollTop - view.clientHeight < 24;
      view.replaceChildren();
      rows.slice(-800).forEach((entry) => {
        const line = document.createElement("div");
        line.className = `whitespace-pre-wrap break-all ${logLevelClass(entry.level)}`;
        const when = entry.time ? String(entry.time).replace("T", " ").replace("+0000", "Z") : "";
        const where = [entry.process, entry.subsystem, entry.category].filter(Boolean).join(" ");
        line.textContent = `${when}  ${entry.level}  ${where}  ${entry.message}`.trim();
        view.appendChild(line);
      });
      if (!rows.length) {
        const empty = document.createElement("div");
        empty.className = "text-slate-500";
        empty.textContent = logEntries.length ? "No lines match the current filters." : "Waiting for log lines…";
        view.appendChild(empty);
      }
      if (logAutoScroll && (atBottom || logEntries.length < 40)) view.scrollTop = view.scrollHeight;
    }

    function scheduleLogs() {
      if (logRenderQueued) return;
      logRenderQueued = true;
      setTimeout(() => {
        logRenderQueued = false;
        renderLogs();
      }, 250);
    }

    function startLogs() {
      logStopRequested = false;
      if (logSocket && (logSocket.readyState === WebSocket.OPEN || logSocket.readyState === WebSocket.CONNECTING)) return;
      const url = `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws/logs`;
      $("log-status").textContent = "Connecting to log stream…";
      logSocket = socket(url);
      let logsOpened = false;
      logSocket.onopen = () => {
        logsOpened = true;
        $("log-status").textContent = logPaused ? "Paused" : "Live";
      };
      logSocket.onmessage = (event) => {
        let payload;
        try {
          payload = JSON.parse(event.data);
        } catch (error) {
          return;
        }
        if (payload && payload.error && !payload.eventMessage) {
          $("log-status").textContent = payload.error;
          return;
        }
        if (logPaused) return;
        logEntries.push(logRecord(payload));
        if (logEntries.length > LOG_LIMIT) logEntries.splice(0, logEntries.length - LOG_LIMIT);
        scheduleLogs();
      };
      logSocket.onclose = async () => {
        logSocket = null;
        if (logStopRequested || sessionDead || $("view-logs").classList.contains("hidden")) return;
        if (!logsOpened && await sessionRejected()) {
          showSessionExpired();
          $("log-status").textContent = "Reload the page to stream logs";
          return;
        }
        $("log-status").textContent = "Reconnecting…";
        setTimeout(() => {
          if (!logStopRequested && !sessionDead && !$("view-logs").classList.contains("hidden")) startLogs();
        }, 1000);
      };
    }

    function stopLogs() {
      logStopRequested = true;
      if (!logSocket) return;
      const socket = logSocket;
      logSocket = null;
      socket.onclose = null;
      socket.close();
      $("log-status").textContent = "Stream starts when this tab is open";
    }

    function formatStorage(value) {
      if (value == null || !Number.isFinite(Number(value))) return "—";
      if (Number(value) >= 1e9) return `${(Number(value)/1e9).toFixed(2)} GB`;
      if (Number(value) >= 1e6) return `${(Number(value)/1e6).toFixed(1)} MB`;
      if (Number(value) >= 1e3) return `${(Number(value)/1e3).toFixed(1)} kB`;
      return `${Number(value)} B`;
    }

    function formatBytesExact(bytes) {
      const value = Number(bytes) || 0;
      if (value <= 0) return "0 B";
      return formatStorage(value);
    }

    function renderApfs(body) {
      const list = $("apfs-list");
      list.replaceChildren();
      const containers = (body.containers || []).filter((item) => (item.size_bytes || 0) >= 1024 ** 3).slice(0, 4);
      $("storage-caption").textContent = containers.length
        ? `${containers.length} large container${containers.length === 1 ? "" : "s"} · ${ (body.mounts || []).length } mounts from df`
        : "No large APFS containers reported";
      if (body.status && body.status !== "success") $("storage-caption").textContent = `${body.status}: ${body.list_note || "Some APFS data unavailable"}`;
      if (!containers.length) {
        const empty = document.createElement("p");
        empty.className = "text-sm text-slate-500";
        empty.textContent = body.list_note || "diskutil did not return container sizes.";
        list.appendChild(empty);
        return;
      }
      containers.forEach((container) => {
        const card = document.createElement("div");
        card.className = "rounded-xl border border-slate-800 bg-slate-950/40 p-4";
        const title = document.createElement("div");
        title.className = "flex flex-wrap items-baseline justify-between gap-2";
        const name = document.createElement("p");
        name.className = "text-sm font-medium text-white";
        name.textContent = container.device || "APFS container";
        const meta = document.createElement("p");
        meta.className = "metric text-xs text-slate-400";
        const purge = container.purgeable_bytes;
        meta.textContent = purge
          ? `${formatBytesExact(container.used_bytes)} physically allocated · ${formatBytesExact(container.free_bytes)} physically free · ${formatBytesExact(purge)} purgeable`
          : `${formatBytesExact(container.used_bytes)} physically allocated · ${formatBytesExact(container.free_bytes)} physically free`;
        title.append(name, meta);
        if (container.available_bytes != null) {
          const capacity=document.createElement("p");capacity.className="text-xs text-emerald-300";
          capacity.textContent=`${formatStorage(container.available_bytes)} available including ${formatStorage(container.reclaimable_bytes)} reclaimable headroom`;
          title.append(capacity);
        }
        const track = document.createElement("div");
        track.className = "mt-3 flex h-3 overflow-hidden rounded-full bg-slate-800";
        const total = Math.max(1, Number(container.size_bytes) || 1);
        const purgeBytes = Math.min(Math.max(0, Number(purge) || 0), Number(container.used_bytes) || 0);
        const usedBytes = Math.max(0, (Number(container.used_bytes) || 0) - purgeBytes);
        const freeBytes = Math.max(0, Number(container.free_bytes) || 0);
        [["#3b82f6", usedBytes], ["#f59e0b", purgeBytes], ["#334155", freeBytes]].forEach(([color, bytes]) => {
          if (!bytes) return;
          const seg = document.createElement("div");
          seg.style.width = `${(bytes / total) * 100}%`;
          seg.style.background = color;
          track.appendChild(seg);
        });
        const volumes = document.createElement("ul");
        volumes.className = "mt-3 space-y-1 text-xs text-slate-400";
        (container.volumes || []).slice(0, 8).forEach((volume) => {
          const item = document.createElement("li");
          item.className = "flex justify-between gap-3";
          const label = document.createElement("span");
          label.textContent = `${volume.name || volume.device}${volume.role ? ` · ${volume.role}` : ""}`;
          const size = document.createElement("span");
          size.className = "metric text-slate-300";
          size.textContent = formatBytesExact(volume.used_bytes);
          item.append(label, size);
          volumes.appendChild(item);
        });
        card.append(title, track, volumes);
        list.appendChild(card);
      });
    }

    function renderDirectories(body) {
      const list = $("dir-list");
      list.replaceChildren();
      const directories = body.directories || [];
      const maxBytes = Math.max(1, ...directories.map((item) => Number(item.bytes) || 0));
      $("dir-caption").textContent = body.cached
        ? "Cached scan · Applications, Library, Downloads, and Documents"
        : "Applications, Library, Downloads, and Documents · depth 2";
      directories.forEach((dir) => {
        const row = document.createElement("div");
        const head = document.createElement("div");
        head.className = "mb-1 flex items-baseline justify-between gap-3 text-sm";
        const path = document.createElement("span");
        path.className = "truncate font-mono text-slate-200";
        path.textContent = dir.path;
        const size = document.createElement("span");
        size.className = "metric text-slate-300";
        size.textContent = dir.bytes == null ? "Scanning…" : formatBytesExact(dir.bytes);
        head.append(path, size, revealFolderButton(dir.path));
        const track = document.createElement("div");
        track.className = "bar-track h-1.5";
        const fill = document.createElement("div");
        fill.className = "bar-fill";
        fill.style.width = `${((Number(dir.bytes) || 0) / maxBytes) * 100}%`;
        fill.style.background = "#3b82f6";
        track.appendChild(fill);
        row.append(head, track);
        const comparison=document.createElement('p');comparison.className='mt-2 text-xs text-slate-400';
        comparison.textContent=dir.delta_bytes==null?(dir.comparison_note||'Comparison pending'): `${storageDelta(dir.delta_bytes)} since ${new Date(dir.previous_scanned_at).toLocaleString()}`;
        row.append(comparison);
        if (dir.error) { const warning=document.createElement("p"); warning.className="mt-2 text-xs text-amber-300"; warning.textContent=`Partial/unavailable: ${dir.error}`; row.append(warning); }
        const kids = (dir.children || []).filter((child) => child.depth === 1).slice(0, 200);
        if (dir.children_truncated) { const note=document.createElement("p");note.className="text-xs text-slate-500";note.textContent="Showing the largest 200 child directories.";row.append(note); }
        if (kids.length) {
          const nested = document.createElement("ul");
          nested.className = "mt-2 space-y-1 pl-3 text-xs text-slate-500";
          kids.forEach((child) => {
            const item = document.createElement("li");
            item.className = "flex justify-between gap-3";
            const label = document.createElement("button");
            label.type = "button";
            label.disabled = body.status === "running";
            label.className = "truncate font-mono text-blue-300 underline";
            label.onclick = () => {storagePath=child.path; loadStorage(true);};
            label.textContent = child.path;
            const childSize = document.createElement("span");
            childSize.className = "metric text-slate-400";
            childSize.textContent = formatBytesExact(child.bytes);
            if(child.delta_bytes!=null) childSize.textContent+=` (${storageDelta(child.delta_bytes)})`;
            item.append(label, childSize, revealFolderButton(child.path));
            nested.appendChild(item);
          });
          row.appendChild(nested);
        }
        list.appendChild(row);
      });
    }

    async function loadStorage(force) {
      if (storagePromise) return storagePromise;
      if (!force && Date.now()-storageLoadedAt < 120000) return;
      $("dir-caption").textContent = "Starting progressive scan…";
      storagePromise = (async () => {
        const apfs = await apiJSON("/api/storage/apfs");
        renderApfs(apfs);
        const state = await apiJSON("/api/storage/scans", {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({path:storagePath, force:Boolean(force)})});
        renderScan(state);
        storageLoadedAt = Date.now();
      })().catch(error => {$("dir-caption").textContent=error.message;}).finally(()=>{storagePromise=null;});
      return storagePromise;
    }

    function storageDelta(bytes) {return bytes===0?'No change':`${bytes>0?'+':'−'}${formatBytesExact(Math.abs(bytes))}`;}
    function revealFolderButton(path) {
      const button=document.createElement('button');button.type='button';button.className='btn-ghost text-xs';button.textContent='Reveal';button.setAttribute('aria-label',`Reveal ${path} in Finder`);
      button.onclick=async()=>{button.disabled=true;try {await apiJSON('/api/storage/reveal',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({path})});}catch(error){showToast(error.message);}finally{button.disabled=false;}};
      return button;
    }
    function renderStorageBreadcrumbs(state) {
      const nav=$('storage-breadcrumbs');nav.replaceChildren();
      const add=(label,path)=>{const button=document.createElement('button');button.type='button';button.className='btn-ghost';button.textContent=label;button.disabled=state.status==='running';button.onclick=()=>{storagePath=path;storageLoadedAt=0;loadStorage(false);};nav.append(button);};
      add('Root folders',null);
      const target=state.targets?.length===1?state.targets[0]:null;
      if(!target) return;
      const root=(state.roots||[]).find(root=>target===root||target.startsWith(root+'/'));
      if(!root) return;
      add(root,root);
      let path=root;
      for(const part of target.slice(root.length).split('/').filter(Boolean)) {path+='/'+part;add(part,path);}
      nav.lastElementChild?.setAttribute('aria-current','location');
    }
    function renderScan(state) {
      renderStorageBreadcrumbs(state);
      renderDirectories(state);
      $("dir-caption").textContent = `${state.status} · ${state.completed || 0}/${state.total || 0} folders · ${state.scanned_at || ""}${state.cached ? " · cached" : ""}${state.error ? ` · ${state.error}` : ""}`;
      $("storage-cancel").disabled = state.status !== "running";
      $("storage-refresh").disabled = state.status === "running";
      $("storage-roots").disabled = state.status === "running";
      clearTimeout(storagePoll);
      if (state.status === "running") storagePoll = setTimeout(async()=>{
        try { renderScan(await apiJSON("/api/storage/scans")); }
        catch(error) {$("dir-caption").textContent=error.message;}
      },1000);
    }

    function propertyCard(title, fields) {
      const card = document.createElement("article");
      card.className = "rounded-xl border border-slate-800 bg-slate-950/40 p-4";
      const heading = document.createElement("h3");
      heading.className = "mb-3 text-sm font-medium text-white";
      heading.textContent = title || "Details";
      const grid = document.createElement("dl");
      grid.className = "grid gap-2 sm:grid-cols-2";
      (fields || []).forEach((field) => {
        const wrap = document.createElement("div");
        const dt = document.createElement("dt");
        dt.className = "text-[11px] uppercase tracking-[0.08em] text-slate-500";
        dt.textContent = field.key;
        const dd = document.createElement("dd");
        dd.className = "mt-0.5 break-all text-sm text-slate-100";
        dd.textContent = String(field.value);
        wrap.append(dt, dd);
        grid.appendChild(wrap);
      });
      if (!(fields || []).length) {
        const empty = document.createElement("p");
        empty.className = "text-sm text-slate-500";
        empty.textContent = "No fields in this section.";
        card.append(heading, empty);
        return card;
      }
      card.append(heading, grid);
      return card;
    }

    function appKind(app) {
      if (app.kind === "system" || app.kind === "third-party") return app.kind;
      const source = String(app.obtained_from || "").toLowerCase();
      const path = app.path || "";
      if (source === "apple" || path.startsWith("/System/") || path.startsWith("/usr/") || path.startsWith("/Library/Apple/")) return "system";
      return "third-party";
    }

    function appSource(app) {
      const labels = {
        apple: "Apple",
        mac_app_store: "Mac App Store",
        identified_developer: "Identified Developer",
        unknown: "Unknown",
      };
      const key = String(app.obtained_from || "").toLowerCase();
      return labels[key] || (app.obtained_from ? String(app.obtained_from).replaceAll("_", " ") : "—");
    }

    function compareVersions(left, right) {
      const a = String(left || "").match(/\d+/g) || [];
      const b = String(right || "").match(/\d+/g) || [];
      const count = Math.max(a.length, b.length);
      for (let index = 0; index < count; index += 1) {
        const difference = Number(a[index] || 0) - Number(b[index] || 0);
        if (difference) return difference;
      }
      return String(left || "").localeCompare(String(right || ""), undefined, { sensitivity: "base" });
    }

    function compareApps(left, right) {
      const key = $("app-sort").value;
      const direction = $("app-order").value === "desc" ? -1 : 1;
      let result = 0;
      if (key === "version") result = compareVersions(left.version, right.version);
      else if (key === "path") result = String(left.path || "").localeCompare(String(right.path || ""), undefined, { sensitivity: "base" });
      else if (key === "obtained_from") result = appSource(left).localeCompare(appSource(right), undefined, { sensitivity: "base" });
      else result = String(left.name || "").localeCompare(String(right.name || ""), undefined, { sensitivity: "base", numeric: true });
      if (!result && key !== "name") {
        result = String(left.name || "").localeCompare(String(right.name || ""), undefined, { sensitivity: "base", numeric: true });
      }
      return result * direction;
    }

    function renderSystem() {
      const body = $("sys-body");
      body.className = "space-y-4";
      body.replaceChildren();
      if (!systemInfo) {
        body.className = "tile p-5 text-sm text-slate-400";
        body.textContent = "System Information is still loading.";
        return;
      }
      const data = systemInfo;
      if (systemSection === "hardware") {
        body.append(propertyCard("Hardware", data.hardware && data.hardware.fields));
        if (data.software) body.append(propertyCard("Software", data.software.fields));
      } else if (systemSection === "displays") {
        (data.displays || []).forEach((gpu) => {
          body.append(propertyCard(gpu.name, gpu.fields));
          (gpu.displays || []).forEach((display) => body.append(propertyCard(display.name, display.fields)));
        });
        if (!(data.displays || []).length) body.append(propertyCard("Displays", []));
      } else if (systemSection === "storage") {
        (data.storage || []).forEach((item) => body.append(propertyCard(item.name, item.fields)));
        if (!(data.storage || []).length) body.append(propertyCard("Storage", []));
      } else if (systemSection === "network") {
        (data.network || []).forEach((item) => {
          const fields = (item.fields || []).slice();
          if ((item.addresses || []).length) fields.unshift({ key: "IPv4", value: item.addresses.join(", ") });
          body.append(propertyCard(item.name, fields));
        });
        if (!(data.network || []).length) body.append(propertyCard("Network", []));
      } else {
        const table = document.createElement("div");
        table.className = "tile overflow-auto p-4";
        const query = $("app-search").value.toLowerCase();
        const kind = $("app-kind").value;
        const inventory = data.applications || [];
        const rows = inventory.filter((app) => {
          if (kind !== "all" && appKind(app) !== kind) return false;
          return !query || `${app.name} ${app.path} ${appSource(app)}`.toLowerCase().includes(query);
        }).sort(compareApps);
        const caption = document.createElement("p");
        caption.className = "mb-3 text-xs text-slate-500";
        const kindLabel = kind === "system" ? "system" : kind === "third-party" ? "third-party" : "";
        const shown = Math.min(rows.length, 500);
        caption.textContent = `${kindLabel ? `${shown} ${kindLabel} of ${inventory.length}` : `${shown} of ${inventory.length}`} applications${rows.length > 500 ? " · showing first 500; narrow the search" : ""}${data.cached ? " · cached" : ""}`;
        const list = document.createElement("table");
        list.className = "w-full min-w-[640px] text-left text-sm";
        const head = document.createElement("thead");
        head.innerHTML = '<tr class="text-[11px] uppercase tracking-[0.08em] text-slate-400"><th class="px-2 py-2">Name</th><th class="px-2 py-2">Version</th><th class="px-2 py-2">Source</th><th class="px-2 py-2">Path</th></tr>';
        const tbody = document.createElement("tbody");
        rows.slice(0,500).forEach((app) => {
          const tr = document.createElement("tr");
          tr.className = "border-t border-slate-800/70";
          const cells = [app.name, app.version, appSource(app), app.path];
          cells.forEach((value) => {
            const td = document.createElement("td");
            td.className = "max-w-xs truncate px-2 py-2 text-slate-200";
            td.textContent = value || "—";
            td.title = value || "";
            tr.appendChild(td);
          });
          tbody.appendChild(tr);
        });
        list.append(head, tbody);
        table.append(caption, list);
        body.appendChild(table);
      }
    }

    async function loadSystemInfo(force) {
      if (systemInfo && !force && Date.now()-systemLoadedAt < 300000) {
        renderSystem();
        return;
      }
      if (systemPromise) return systemPromise;
      $("sys-body").className = "tile p-5 text-sm text-slate-400";
      $("sys-body").textContent = "Reading system_profiler. The first pass includes installed applications and can take a minute.";
      systemPromise = (async () => {
        const response = await fetch(`/api/system-info?force=${Boolean(force)}`);
        const body = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(errorDetail(body, "System Information failed"));
        systemInfo = body;
        systemLoadedAt = Date.now();
        $("system-age").textContent = `Checked ${body.checked_at}${body.cached ? " · cached" : ""}`;
        renderSystem();
      })().catch((error) => {
        $("sys-body").className = "tile p-5 text-sm text-red-300";
        $("sys-body").textContent = error.message || "System Information failed.";
        systemPromise = null;
      }).finally(()=>{systemPromise=null;});
      return systemPromise;
    }

    function compareInstalledApps(left, right) {
      const key = $("apps-sort").value;
      const direction = $("apps-order").value === "desc" ? -1 : 1;
      let result = 0;
      if (key === "version") result = String(left.version || "").localeCompare(String(right.version || ""), undefined, { numeric: true, sensitivity: "base" });
      else if (key === "path") result = String(left.path || "").localeCompare(String(right.path || ""), undefined, { sensitivity: "base" });
      else if (key === "app_store") result = String(left.app_store || "").localeCompare(String(right.app_store || ""), undefined, { sensitivity: "base" });
      else if (key === "signed") result = String(left.signed || "").localeCompare(String(right.signed || ""), undefined, { sensitivity: "base" });
      else if (key === "developer") result = String(left.developer || "").localeCompare(String(right.developer || ""), undefined, { sensitivity: "base" });
      else if (key === "team_id") result = String(left.team_id || "").localeCompare(String(right.team_id || ""), undefined, { sensitivity: "base" });
      else if (key === "architecture") result = String(left.architecture || "").localeCompare(String(right.architecture || ""), undefined, { sensitivity: "base" });
      else result = String(left.name || "").localeCompare(String(right.name || ""), undefined, { sensitivity: "base", numeric: true });
      if (!result && key !== "name") {
        result = String(left.name || "").localeCompare(String(right.name || ""), undefined, { sensitivity: "base", numeric: true });
      }
      return result * direction;
    }

    function revealApplicationButton(path) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "btn-ghost px-2 py-1 text-[11px]";
      button.textContent = "Reveal";
      button.title = "Reveal in Finder";
      button.onclick = async () => {
        button.disabled = true;
        try {
          await apiJSON("/api/applications/reveal", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ path }),
          });
        } catch (error) {
          showToast(error.message);
        } finally {
          button.disabled = false;
        }
      };
      return button;
    }

    function renderInstalledApps() {
      const body = $("apps-body");
      if (!installedApps) {
        body.className = "tile p-5 text-sm text-slate-400";
        body.textContent = "Installed applications are still loading.";
        return;
      }
      const inventory = installedApps.applications || [];
      const query = ($("apps-search").value || "").toLowerCase();
      const store = $("apps-store").value;
      const signed = $("apps-signed").value;
      const location = $("apps-location").value;
      const rows = inventory.filter((app) => {
        if (store !== "all" && app.app_store !== store) return false;
        if (signed !== "all" && app.signed !== signed) return false;
        if (location !== "all" && app.location !== location) return false;
        if (!query) return true;
        const haystack = [app.name, app.version, app.developer, app.team_id, app.bundle_id, app.path, app.architecture, app.app_store, app.signed]
          .filter(Boolean)
          .join(" ")
          .toLowerCase();
        return haystack.includes(query);
      }).sort(compareInstalledApps);
      const shown = Math.min(rows.length, 500);
      body.className = "tile overflow-auto p-4";
      body.replaceChildren();
      const caption = document.createElement("p");
      caption.className = "mb-3 text-xs text-slate-500";
      const extra = [];
      if (installedApps.cached) extra.push("cached");
      if (installedApps.errors) extra.push(`${installedApps.errors} read errors`);
      caption.textContent = `${shown} of ${inventory.length} applications${rows.length > 500 ? " · showing first 500; narrow the search" : ""}${extra.length ? ` · ${extra.join(" · ")}` : ""}`;
      const table = document.createElement("table");
      table.className = "w-full min-w-[1100px] text-left text-sm";
      const head = document.createElement("thead");
      head.innerHTML = '<tr class="text-[11px] uppercase tracking-[0.08em] text-slate-400"><th class="px-2 py-2">Name</th><th class="px-2 py-2">Version</th><th class="px-2 py-2">App Store</th><th class="px-2 py-2">Signed</th><th class="px-2 py-2">Developer</th><th class="px-2 py-2">Team ID</th><th class="px-2 py-2">Arch</th><th class="px-2 py-2">Path</th><th class="px-2 py-2">Reveal</th></tr>';
      const tbody = document.createElement("tbody");
      rows.slice(0, 500).forEach((app) => {
        const tr = document.createElement("tr");
        tr.className = "border-t border-slate-800/70";
        [
          app.name,
          app.version,
          app.app_store,
          app.signed,
          app.developer,
          app.team_id,
          app.architecture,
          app.path,
        ].forEach((value, index) => {
          const td = document.createElement("td");
          td.className = index === 7 ? "max-w-[16rem] truncate px-2 py-2 font-mono text-[11px] text-slate-400" : "max-w-[12rem] truncate px-2 py-2 text-slate-200";
          td.textContent = value || "—";
          td.title = value || "";
          tr.appendChild(td);
        });
        const action = document.createElement("td");
        action.className = "px-2 py-2";
        if (app.path) action.appendChild(revealApplicationButton(app.path));
        tr.appendChild(action);
        tbody.appendChild(tr);
      });
      table.append(head, tbody);
      body.append(caption, table);
    }

    async function loadInstalledApps(force) {
      if (installedApps && !force && Date.now() - installedAppsLoadedAt < 300000) {
        renderInstalledApps();
        return;
      }
      if (installedAppsPromise) return installedAppsPromise;
      $("apps-body").className = "tile p-5 text-sm text-slate-400";
      $("apps-body").textContent = "Scanning /Applications and ~/Applications. Reading codesign metadata can take a minute on the first pass.";
      installedAppsPromise = (async () => {
        const response = await fetch(`/api/applications?force=${Boolean(force)}`);
        const body = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(errorDetail(body, "Applications inventory failed"));
        installedApps = body;
        installedAppsLoadedAt = Date.now();
        $("apps-age").textContent = `Checked ${body.checked_at}${body.cached ? " · cached" : ""} · ${body.count || 0} apps`;
        renderInstalledApps();
      })().catch((error) => {
        $("apps-body").className = "tile p-5 text-sm text-red-300";
        $("apps-body").textContent = error.message || "Applications inventory failed.";
        installedAppsPromise = null;
      }).finally(() => { installedAppsPromise = null; });
      return installedAppsPromise;
    }

    document.querySelectorAll("[data-tab]").forEach((button) => {
      button.addEventListener("click", () => showTab(button.dataset.tab));
    });
    $("proc-flat").addEventListener("click", () => setProcessView("flat"));
    $("proc-tree").addEventListener("click", () => setProcessView("tree"));
    $("proc-filter").addEventListener("input", ()=>{processPage=0;renderProcesses();});
    $("proc-body").addEventListener("click", (event) => {
      const button = event.target.closest("button[data-pid]");
      if (button) {
        openModal(button.dataset.pid, button.dataset.name, button.dataset.created);
        return;
      }
      const row = event.target.closest("tr[data-pid]");
      if (row?.dataset.grouped === "true") {
        $("proc-group").checked=false;$("proc-filter").value=row.dataset.application;processPage=0;renderProcesses();
      } else if (row) openInspector(row.dataset.pid, row.dataset.name);
    });
    $("inspector-close").addEventListener("click", closeInspector);
    $("modal-cancel").addEventListener("click", closeModal);
    $("modal").addEventListener("click", (event) => {
      if (event.target.closest("[data-close]")) closeModal();
    });
    $("modal-confirm").addEventListener("click", confirmKill);
    document.addEventListener("keydown", (event) => {
      if (event.key !== "Escape") return;
      if (!$("modal").classList.contains("hidden")) {
        closeModal();
        return;
      }
      if (!$("inspector").classList.contains("hidden")) closeInspector();
    });
    ["log-level", "log-subsystem", "log-process", "log-category", "log-search"].forEach((id) => {
      $(id).addEventListener("input", renderLogs);
      $(id).addEventListener("change", renderLogs);
    });
    $("log-pause").addEventListener("click", () => {
      logPaused = !logPaused;
      $("log-pause").textContent = logPaused ? "Resume" : "Pause";
      if (logSocket && logSocket.readyState === WebSocket.OPEN) {
        $("log-status").textContent = logPaused ? "Paused" : "Live";
      }
      if (!logPaused) renderLogs();
    });
    $("log-clear").addEventListener("click", () => {
      logEntries = [];
      renderLogs();
    });
    $("log-scroll").addEventListener("click", () => {
      logAutoScroll = !logAutoScroll;
      $("log-scroll").textContent = logAutoScroll ? "Auto-scroll on" : "Auto-scroll off";
      $("log-scroll").className = logAutoScroll
        ? "btn-ghost bg-blue-600/20 px-3 py-1.5 text-xs text-blue-300"
        : "btn-ghost px-3 py-1.5 text-xs";
      if (logAutoScroll) {
        const view = $("log-view");
        view.scrollTop = view.scrollHeight;
      }
    });
    $("storage-refresh").addEventListener("click", () => {
      loadStorage(true);
    });
    document.querySelectorAll("[data-sys]").forEach((button) => {
      button.addEventListener("click", () => {
        systemSection = button.dataset.sys;
        document.querySelectorAll("[data-sys]").forEach((item) => {
          item.className = item.dataset.sys === systemSection
            ? "sys-tab rounded-lg bg-blue-600/20 px-3 py-1.5 text-xs font-medium text-blue-300"
            : "sys-tab rounded-lg px-3 py-1.5 text-xs font-medium text-slate-400";
        });
        if (systemInfo) renderSystem();
      });
    });
    $("nq-btn").addEventListener("click", runNetworkQuality);
    $("su-btn").addEventListener("click", loadSoftwareUpdates);


