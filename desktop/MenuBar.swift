import AppKit

final class AppDelegate: NSObject, NSApplicationDelegate {
    private var item: NSStatusItem!
    private var opening = false
    private var root: URL {
        let file = Bundle.main.url(forResource: "project-root", withExtension: "txt")!
        let path = (try? String(contentsOf: file, encoding: .utf8)) ?? ""
        return URL(fileURLWithPath: path.trimmingCharacters(in: .whitespacesAndNewlines))
    }
    func applicationDidFinishLaunching(_ notification: Notification) {
        item = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        item.button?.title = "◉ Sentinel"
        item.button?.toolTip = "macOS Sentinel dashboard"
        let menu = NSMenu()
        let open = NSMenuItem(title: "Open Dashboard", action: #selector(openDashboard), keyEquivalent: "o")
        open.target = self; menu.addItem(open)
        let folder = NSMenuItem(title: "Show Project Folder", action: #selector(showFolder), keyEquivalent: "")
        folder.target = self; menu.addItem(folder)
        menu.addItem(NSMenuItem.separator())
        let note = NSMenuItem(title: "Dashboard server stays running when menu closes", action: nil, keyEquivalent: "")
        menu.addItem(note)
        let quit = NSMenuItem(title: "Quit Menu Bar Shortcut", action: #selector(quitApp), keyEquivalent: "q")
        quit.target = self; menu.addItem(quit)
        item.menu = menu
    }
    @objc func openDashboard() {
        if opening { return }
        opening = true
        item.button?.title = "◉ Opening…"
        let process = Process()
        process.executableURL = root.appendingPathComponent(".venv/bin/python")
        process.arguments = ["-B", root.appendingPathComponent("desktop/launch.py").path]
        let errorPipe = Pipe()
        process.standardError = errorPipe
        process.standardOutput = FileHandle.nullDevice
        process.terminationHandler = { [weak self] child in
            let message = String(data: errorPipe.fileHandleForReading.readDataToEndOfFile(), encoding: .utf8) ?? "Startup failed"
            DispatchQueue.main.async {
                self?.opening = false; self?.item.button?.title = "◉ Sentinel"
                if child.terminationStatus != 0 { self?.showError(message) }
            }
        }
        do { try process.run() }
        catch { opening = false; item.button?.title = "◉ Sentinel"; showError(error.localizedDescription) }
    }
    private func showError(_ message: String) {
        NSApp.activate(ignoringOtherApps: true)
        let alert = NSAlert(); alert.messageText = "Could not open dashboard"; alert.informativeText = message; alert.runModal()
    }
    @objc func showFolder() { NSWorkspace.shared.open(root) }
    @objc func quitApp() { NSApp.terminate(nil) }
}
let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
app.setActivationPolicy(.accessory)
app.run()
