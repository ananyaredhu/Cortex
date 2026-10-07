export interface CardProps {
  tone?: 'card' | 'sunken' | 'inverse' | 'accent';
  padding?: number | string;
  radius?: 'sm' | 'md' | 'lg' | 'xl';
  /** Hover darkens the hairline border (no lift, no shadow) */
  interactive?: boolean;
  onClick?: () => void;
  children?: React.ReactNode;
  style?: React.CSSProperties;
}
export declare function Card(props: CardProps): JSX.Element;