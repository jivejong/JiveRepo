// Colour bands of the galaxy view (doc 07 Phase 7, panel 1; doc 06 "Active anomaly": 4.0 is the anomaly band, 5.75 the emergency band).
// The server decides the band; this module only names it, and never recomputes a threshold.

export const BAND_CLASS = {
  normal: "band-normal",
  anomaly: "band-anomaly",
  emergency: "band-emergency",
  no_data: "band-no-data",
};

const LABEL = { normal: "normal", anomaly: "anomaly", emergency: "emergency", no_data: "no data" };

/** The CSS class of a band; an unknown band is shown as no data. */
export function bandClass(band) {
  return BAND_CLASS[band] || BAND_CLASS.no_data;
}

export function bandLabel(band) {
  return LABEL[band] || LABEL.no_data;
}
