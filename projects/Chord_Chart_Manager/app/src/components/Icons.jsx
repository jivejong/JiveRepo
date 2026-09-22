/** Minimal inline SVG icons — no external dependency */
import React from 'react';

const Icon = ({ d, size = 22, ...props }) => (
  <svg width={size} height={size} viewBox="0 0 24 24" fill="none"
    stroke="currentColor" strokeWidth={1.75} strokeLinecap="round" strokeLinejoin="round" {...props}>
    <path d={d} />
  </svg>
);

export const SearchIcon   = p => <Icon {...p} d="M21 21l-4.35-4.35M17 11A6 6 0 1 1 5 11a6 6 0 0 1 12 0z" />;
export const FilterIcon   = p => <Icon {...p} d="M22 3H2l8 9.46V19l4 2v-8.54L22 3z" />;
export const MusicIcon    = p => <Icon {...p} d="M9 18V5l12-2v13M9 18a3 3 0 1 1-6 0 3 3 0 0 1 6 0zm12-2a3 3 0 1 1-6 0 3 3 0 0 1 6 0z" />;
export const ListIcon     = p => <Icon {...p} d="M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01" />;
export const SettingsIcon = p => <Icon {...p} d="M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6zm6.93-1A7 7 0 0 0 19 12a7 7 0 0 0-.07-1L21 9.22A1 1 0 0 0 21.27 8l-2-3.46a1 1 0 0 0-1.22-.42l-2.29.92A7 7 0 0 0 14 4.29V2h-4v2.29a7 7 0 0 0-1.76.75l-2.29-.92a1 1 0 0 0-1.22.42L2.73 8a1 1 0 0 0 .27 1.22L5 11a7 7 0 0 0-.07 1 7 7 0 0 0 .07 1L3 14.78a1 1 0 0 0-.27 1.22l2 3.46a1 1 0 0 0 1.22.42l2.29-.92a7 7 0 0 0 1.76.75V22h4v-2.29a7 7 0 0 0 1.76-.75l2.29.92a1 1 0 0 0 1.22-.42l2-3.46a1 1 0 0 0-.27-1.22z" />;
export const ChevronRight = p => <Icon {...p} d="M9 18l6-6-6-6" />;
export const ChevronLeft  = p => <Icon {...p} d="M15 18l-6-6 6-6" />;
export const ChevronDown  = p => <Icon {...p} d="M6 9l6 6 6-6" />;
export const ChevronUp    = p => <Icon {...p} d="M18 15l-6-6-6 6" />;
export const ArrowLeft    = p => <Icon {...p} d="M19 12H5M12 19l-7-7 7-7" />;
export const PlusIcon     = p => <Icon {...p} d="M12 5v14M5 12h14" />;
export const XIcon        = p => <Icon {...p} d="M18 6L6 18M6 6l12 12" />;
export const EditIcon     = p => <Icon {...p} d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z" />;
export const TrashIcon    = p => <Icon {...p} d="M3 6h18M8 6V4h8v2M19 6l-1 14H6L5 6" />;
export const CapoIcon     = p => <Icon {...p} d="M12 2v20M2 12h20" strokeWidth={2.5} />;
export const SyncIcon     = p => <Icon {...p} d="M23 4v6h-6M1 20v-6h6M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15" />;
export const GuitarIcon   = p => <Icon {...p} d="M3.5 20.5l3-3M9 15l-3.5 3.5M14.5 8.5l3-3M17.5 5.5C18.33 4.67 19.5 4 21 4s2.5.5 2.5 2.5-2 3.5-2 3.5l-4-4zM9 15c-1.5-1.5-2-4 .5-6.5s5-2 6.5-.5" />;
export const CheckIcon    = p => <Icon {...p} d="M20 6L9 17l-5-5" />;
