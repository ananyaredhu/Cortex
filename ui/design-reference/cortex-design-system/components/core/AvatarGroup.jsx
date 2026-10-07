import React from 'react';
import { Avatar } from './Avatar.jsx';
export function AvatarGroup({ people = [], size = 28, max = 4 }) {
  const shown = people.slice(0, max), extra = people.length - shown.length;
  return (
    <span style={{ display: 'inline-flex', alignItems: 'center' }}>
      {shown.map((p, i) => <Avatar key={i} {...p} size={size} style={{ marginLeft: i ? -size * 0.28 : 0, boxShadow: '0 0 0 2px var(--surface-card), inset 0 0 0 1px var(--border-subtle)' }} />)}
      {extra > 0 && <span style={{ marginLeft: -size * 0.28, width: size, height: size, borderRadius: '50%', background: 'var(--gray-100)', boxShadow: '0 0 0 2px var(--surface-card)', display: 'inline-flex', alignItems: 'center', justifyContent: 'center', font: `700 ${Math.round(size * 0.36)}px/1 var(--font-sans)`, color: 'var(--text-body)' }}>+{extra}</span>}
    </span>
  );
}