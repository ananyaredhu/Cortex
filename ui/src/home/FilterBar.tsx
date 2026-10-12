import { ChevronDown, ListFilter, Plus, Trash2 } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { describe, type Filters, type Option, type Prop, PROP_LABEL, PROP_NOTE, PROPS, SINGLE } from "./filters";

/**
 * The filter bar on My Work: "Add filter" picks a property, and each filter in use is a chip that opens its values.
 * One menu is open at a time; Escape or a click elsewhere closes it.
 */
export function FilterBar({
  value,
  options,
  onChange,
}: {
  value: Filters;
  options: Record<Prop, Option[]>;
  onChange: (next: Filters) => void;
}) {
  const [open, setOpen] = useState<Prop | "add" | null>(null);
  const bar = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const outside = (e: MouseEvent) => {
      if (!bar.current?.contains(e.target as Node)) setOpen(null);
    };
    const escape = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      bar.current?.querySelector<HTMLElement>('[aria-expanded="true"]')?.focus();
      setOpen(null);
    };
    document.addEventListener("mousedown", outside);
    document.addEventListener("keydown", escape);
    return () => {
      document.removeEventListener("mousedown", outside);
      document.removeEventListener("keydown", escape);
    };
  }, [open]);

  const used = PROPS.filter((p) => value[p]);
  const addable = PROPS.filter((p) => !value[p] && options[p].length > 0);
  const without = (p: Prop) => {
    const { [p]: _gone, ...rest } = value;
    return rest;
  };
  const toggle = (p: Prop, v: string) => {
    const now = value[p] ?? [];
    const next = SINGLE.includes(p) ? [v] : now.includes(v) ? now.filter((x) => x !== v) : [...now, v];
    onChange({ ...value, [p]: next });
  };

  return (
    <div className="filters" role="group" aria-label="Filters" ref={bar}>
      <ListFilter size={14} aria-hidden />
      {used.map((p) => (
        <div key={p} className="filters__item">
          <button
            type="button"
            className={`chip${value[p]!.length ? " chip--on" : ""}`}
            aria-haspopup="true"
            aria-expanded={open === p}
            onClick={() => setOpen(open === p ? null : p)}
          >
            {describe(p, value[p]!, options[p])} <ChevronDown size={12} aria-hidden />
          </button>
          {open === p && (
            <div className="filters__menu">
              <fieldset>
                <legend>
                  {PROP_LABEL[p]}
                  {PROP_NOTE[p] && <span className="muted"> · {PROP_NOTE[p]} only</span>}
                </legend>
                {options[p].map((o) => (
                  <label key={o.value}>
                    <input
                      type={SINGLE.includes(p) ? "radio" : "checkbox"}
                      name={`filter-${p}`}
                      checked={value[p]!.includes(o.value)}
                      onChange={() => toggle(p, o.value)}
                    />
                    {o.label}
                  </label>
                ))}
              </fieldset>
              <button
                type="button"
                className="filters__remove"
                onClick={() => {
                  onChange(without(p));
                  setOpen(null);
                }}
              >
                <Trash2 size={13} aria-hidden /> Remove filter
              </button>
            </div>
          )}
        </div>
      ))}

      {addable.length > 0 && (
        <div className="filters__item">
          <button type="button" className="chip filters__add" aria-haspopup="true" aria-expanded={open === "add"} onClick={() => setOpen(open === "add" ? null : "add")}>
            <Plus size={12} aria-hidden /> Add filter
          </button>
          {open === "add" && (
            <div className="filters__menu">
              {addable.map((p) => (
                <button
                  key={p}
                  type="button"
                  onClick={() => {
                    onChange({ ...value, [p]: [] });
                    setOpen(p);
                  }}
                >
                  {PROP_LABEL[p]}
                  {PROP_NOTE[p] && <span className="muted small">{PROP_NOTE[p]}</span>}
                </button>
              ))}
            </div>
          )}
        </div>
      )}

      {used.length > 0 && (
        <button type="button" className="ghost small" onClick={() => onChange({})}>
          Clear all
        </button>
      )}
    </div>
  );
}
