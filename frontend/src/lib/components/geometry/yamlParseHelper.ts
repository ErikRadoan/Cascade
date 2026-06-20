// Thin wrapper around js-yaml — used ONLY for lightweight client-side
// parsing to populate the Template/Object panel UI. The backend's
// /geometry/validate and /geometry/scene endpoints remain authoritative
// for anything that affects the actual simulation. This module must
// never be relied on for correctness, only for fast UI feedback.

import { load } from 'js-yaml';

export default {
  parse(text: string): unknown {
    try {
      return load(text);
    } catch {
      // Invalid YAML mid-typing is expected and frequent — fail silently,
      // the backend validator will surface real errors to the user.
      return null;
    }
  },
};