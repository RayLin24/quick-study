import { slugify } from "./util";

export function describeLocale(locale) {
  return slugify(locale);
}
