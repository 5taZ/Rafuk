function domAppend(target) {
    if (!target) return target;

    const appendChild = (child) => {
        if (child == null || child === false) return;
        if (Array.isArray(child)) {
            for (const nested of child) appendChild(nested);
            return;
        }
        if (child instanceof Node) {
            target.appendChild(child);
            return;
        }
        target.appendChild(document.createTextNode(String(child)));
    };

    for (let i = 1; i < arguments.length; i++) {
        appendChild(arguments[i]);
    }
    return target;
}

function domEl(tagName, options) {
    const node = document.createElement(tagName);
    const config = options || {};

    if (config.className) node.className = config.className;
    if (config.text != null) node.textContent = String(config.text);
    if (config.hidden != null) node.hidden = Boolean(config.hidden);
    if (config.type) node.type = config.type;
    if (config.value != null) node.value = String(config.value);

    if (config.attrs) {
        for (const [name, value] of Object.entries(config.attrs)) {
            if (value == null || value === false) continue;
            node.setAttribute(name, value === true ? "" : String(value));
        }
    }

    if (config.dataset) {
        for (const [name, value] of Object.entries(config.dataset)) {
            if (value == null) continue;
            node.dataset[name] = String(value);
        }
    }

    for (let i = 2; i < arguments.length; i++) {
        domAppend(node, arguments[i]);
    }
    return node;
}

function domClear(node) {
    if (node) node.replaceChildren();
}

function domFragment() {
    const fragment = document.createDocumentFragment();
    for (let i = 0; i < arguments.length; i++) {
        domAppend(fragment, arguments[i]);
    }
    return fragment;
}
