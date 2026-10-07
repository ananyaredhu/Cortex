import { Moon, Sun } from "lucide-react";
import { useEffect, useState } from "react";

type Theme = "light" | "dark";
const KEY = "cortex.theme";

function initial(): Theme {
  try {
    const saved = localStorage.getItem(KEY);
    if (saved === "light" || saved === "dark") return saved;
  } catch {
    // storage unavailable
  }
  return window.matchMedia?.("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

/** Light and dark come from the Cortex tokens (`[data-theme="dark"]` in tokens/colors.css). */
export function ThemeToggle() {
  const [theme, setTheme] = useState<Theme>(initial);
  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    document.documentElement.style.colorScheme = theme;
  }, [theme]);
  const next = theme === "dark" ? "light" : "dark";
  return (
    <button
      type="button"
      className="ghost theme-toggle"
      aria-label={`Switch to ${next} theme`}
      onClick={() => {
        setTheme(next);
        try {
          localStorage.setItem(KEY, next);
        } catch {
          // not remembered
        }
      }}
    >
      {theme === "dark" ? <Sun size={16} aria-hidden /> : <Moon size={16} aria-hidden />}
      {theme === "dark" ? "Light theme" : "Dark theme"}
    </button>
  );
}
