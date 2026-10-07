export interface IconProps {
  /** Lucide icon name, kebab or Pascal case: "search", "file-text", "Database" */
  name: string;
  size?: number;
  /** Default 2 — matches persona line weight at UI size */
  strokeWidth?: number;
  color?: string;
  style?: React.CSSProperties;
}
export declare function Icon(props: IconProps): JSX.Element;