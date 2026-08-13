const { slugify } = require("./util");
const { tag } = require("definitely-not-installed");

function register(name) {
  return slugify(name) + tag;
}

module.exports = { register };
