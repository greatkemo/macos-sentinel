module.exports = {
      darkMode: 'class',
      theme: {
        extend: {
          fontFamily: {
            sans: ['-apple-system', 'BlinkMacSystemFont', '"SF Pro Display"', '"SF Pro Text"', '"Helvetica Neue"', 'sans-serif'],
            mono: ['"SF Mono"', 'ui-monospace', 'Menlo', 'Consolas', 'monospace'],
          },
          colors: {
            ink: '#090d16',
            cpu: '#3b82f6',
            mem: '#8b5cf6',
            net: '#10b981',
            hot: '#ef4444',
            warn: '#f59e0b',
          },
        },
      },
    };
module.exports.content = ["./index.html", "./static/*.js"];
