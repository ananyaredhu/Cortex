import React from 'react';
import { Icon } from '../core/Icon.jsx';
function Row({ item, onSelect }) {
  const [hover, setHover] = React.useState(false);
  return (
    <div role="menuitem" onClick={() => onSelect && onSelect(item)} onMouseEnter={() => setHover(true)} onMouseLeave={() => setHover(false)}
      style={{ display: 'flex', alignItems: 'center', gap: 10, height: 34, padding: '0 10px', borderRadius: 8, cursor: 'pointer',
        background: hover ? 'var(--bg-app)' : 'transparent', color: item.danger ? 'var(--danger)' : 'var(--text-strong)', font: '400 14px/1 var(--font-sans)' }}>
      {item.avatar ? <img src={item.avatar} alt="" style={{ width: 20, height: 20, borderRadius: '50%', background: '#fff', boxShadow: 'inset 0 0 0 1px var(--border-subtle)' }} />
        : item.icon ? <Icon name={item.icon} size={16} color={item.danger ? 'var(--danger)' : 'var(--text-body)'} /> : null}
      <span style={{ flex: 1, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{item.label}</span>
      {item.shortcut && <span style={{ font: '500 11px/1 var(--font-mono)', color: 'var(--text-faint)' }}>{item.shortcut}</span>}
      {item.checked && <Icon name="check" size={16} />}
    </div>
  );
}
export function Menu({ sections = [], onSelect, width = 260, style }) {
  return (
    <div role="menu" style={{ width, padding: 6, background: 'var(--surface-raised)', border: '1px solid var(--border-subtle)', borderRadius: 'var(--radius-md)', boxShadow: 'var(--shadow-2)', ...style }}>
      {sections.map((s, i) => (
        <div key={i} style={{ paddingTop: i ? 6 : 0, marginTop: i ? 6 : 0, borderTop: i ? '1px solid var(--border-subtle)' : 'none' }}>
          {s.title && <div style={{ padding: '6px 10px 4px', font: '400 12px/1 var(--font-sans)', color: 'var(--text-muted)' }}>{s.title}</div>}
          {s.items.map((it, j) => <Row key={j} item={it} onSelect={onSelect} />)}
        </div>
      ))}
    </div>
  );
}