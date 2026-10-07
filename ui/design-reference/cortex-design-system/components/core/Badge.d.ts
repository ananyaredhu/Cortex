export interface BadgeProps {
  tone?: 'neutral' | 'accent' | 'success' | 'danger' | 'info' | 'warning' | 'inverse';
  /** Leading status dot */
  dot?: boolean;
  children?: React.ReactNode;
  style?: React.CSSProperties;
}
export declare function Badge(props: BadgeProps): JSX.Element;