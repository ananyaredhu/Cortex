export interface LogoProps {
  /** Wordmark cap size in px */
  size?: number;
  variant?: 'full' | 'mark';
  /** Puts the mark on a white rounded tile for ink backgrounds */
  onDark?: boolean;
  /** Path to cortex-mark.svg relative to the page */
  markSrc?: string;
  style?: React.CSSProperties;
}
export declare function Logo(props: LogoProps): JSX.Element;