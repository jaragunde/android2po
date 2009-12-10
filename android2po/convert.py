"""Contains the functions that do the hard work.
"""

from itertools import chain
from compat import OrderedDict
from lxml import etree
from babel.messages import pofile, Catalog


__all__ = ('xml2po', 'po2xml',)


WHITESPACE = ' \n\t'     # Whitespace that we collapse
EOF = None


def _load_xml_strings(file):
    """Load all resource names from an Android strings.xml resource file.
    """
    result = OrderedDict()

    try:
        doc = etree.parse(file)
    except etree.XMLSyntaxError, e:
        print "Error: Parsing xml failed: %s" % e
        # Return empty
        return result

    for tag in doc.xpath('/resources/string'):
        if not 'name' in tag.attrib:
            continue
        name = tag.attrib['name']
        if name in result:
            print "Error: %s contains duplicate string names: %s" % (filename, name)
            continue

        def convert_text(text):
            """This is called for every distinct block of text, as they
            are separated by tags.

            It handles most of the Android syntax rules: quoting, escaping,
            collapsing duplicate whitespace etc.
            """
            # '<' and '>' as literal characters inside a text need to be
            # escaped; this is because we need to differentiate them to
            # actual tags inside a resource string which we write to the
            # .po file as literal '<', '>' characters. As a result, if the
            # user puts &lt; inside his Android resource file, this is how
            # it will end up in the .po file as well.
            # We only do this for '<' and '<' right now, which is of course
            # a hack. We'd need to process at least &amp; as well, because
            # right now '&lt;' and '&amp;lt;' both generate the same on
            # import. However, if we were to do that, a simple non-HTML
            # text like "FAQ & Help" would end up us "FAQ &amp; Help" in
            # the .po - not particularly nice.
            # TODO: I can see two approaches to solve this: Handle things
            # differently depending on whether there are nested tags. We'd
            # be able to handle both '&amp;lt;' in a HTML string and output
            # a nice & character in a plaintext string.
            # Option 2: It might be possible to note the type of encoding
            # we did in a .po comment. That would even allow us to present
            # a string containing tags encoded using entities (but not actual
            # nested XML tags) using plain < and > characters in the .po
            # file. Instead of a comment, we could change the import code
            # to require a look at the original resource xml file to
            # determine which kind of encoding was done.
            text = text.replace('<', '&lt;')
            text = text.replace('>', "&gt;")

            # We need to collapse multiple whitespace while paying
            # attention to Android's quoting and escaping.
            space_count = 0
            active_quote = False
            escaped = False
            i = 0
            text = list(text) + [EOF]
            while i < len(text):
                c = text[i]

                # Handle whitespace collapsing
                if c is not EOF and c in WHITESPACE:
                    space_count += 1
                elif space_count > 1:
                    # Remove duplicate whitespace; Pay attention: We
                    # don't do this if we are currently inside a quote,
                    # except for one special case: If we have unbalanced
                    # quotes, e.g. we reach eof while a quote is still
                    # open, we *do* collapse that trailing part; this is
                    # how Android does it, for some reason.
                    if not active_quote or c is EOF:
                        # Replace by a single space, will get rid of
                        # non-significant newlines/tabs etc.
                        text[i-space_count:i] = ' '
                        i -= space_count + 1
                    space_count = 0
                elif space_count == 1:
                    # At this point we have a single whitespace character,
                    # but it might be a newline or tab. If we write this
                    # kind of insignificant whitespace into the .po file,
                    # it will be considered significant on import. So,
                    # make sure that this kind of whitespace is always a
                    # standard space.
                    text[i-1] = ' '
                    space_count = 0
                else:
                    space_count = 0

                # Handle quotes
                if c == '"' and not escaped:
                    active_quote = not active_quote
                    del text[i]
                    i -= 1

                # Handle escapes
                if c == '\\':
                    if not escaped:
                        escaped = True
                    else:
                        # A double-backslash represents a single;
                        # simply deleting the current char will do.
                        del text[i]
                        i -= 1
                        escaped = False
                else:
                    if escaped:
                        # Handle the limited amount of escape codes
                        # that we support.
                        # TODO: What about \r, or \r\n?
                        if c is EOF:
                            # Basically like any other char, but put
                            # this first so we can use the ``in`` operator
                            # in the clauses below without issue.
                            pass
                        elif c == 'n':
                            text[i-1:i+1] = '\n'  # an actual newline
                            i -= 1
                        elif c == 't':
                            text[i-1:i+1] = '\t'  # an actual tab
                            i -= 1
                        elif c in '"\'':
                            text[i-1:i] = ''    # remove the backslash
                            i -= 1
                        else:
                            # All others, we simply keep unmodified.
                            # Android itself actually seems to remove them,
                            # but this is for the developer to resolve;
                            # we're not trying to recreate the Android
                            # parser 100%, merely handle those aspects that
                            # are relevant to convert the text back and
                            # forth without loss.
                            pass
                        escaped = False


                i += 1

            # Join the string together again, but w/o EOF marker
            return "".join(text[:-1])

        # We need to recreate the contents of this tag; this is more
        # complicated as you might expect; firstly, there is nothing
        # built into lxml (or any other parse I have seen for that
        # matter). While it is possible to use the ``etree.tostring``
        # to render this tag and it's children, this still would give
        # us valid XML code; when in fact we want to decode everything
        # XML (including entities), *except* tags. Much more than that
        # though, the processing rules the Android xml format needs
        # require custom processing anyway.
        value = u""
        for ev, elem  in etree.iterwalk(tag, events=('start', 'end',)):
            is_root = elem == tag
            if ev == 'start':
                if not is_root:
                    # TODO: We are currently not dealing correctly with
                    # attribute values that need escaping.
                    params = "".join([" %s=\"%s\"" % (k, v) for k, v in elem.attrib.items()])
                    value += u"<%s%s>" % (elem.tag, params)
                if elem.text is not None:
                    t = elem.text
                    # Leading/Trailing whitespace is removed completely
                    # ONLY if there are now nested tags. Handle this before
                    # calling ``convert_text``, so that whitespace
                    # protecting quotes can still be considered.
                    if elem == tag and len(tag) == 0:
                        t = t.strip(WHITESPACE)
                    value += convert_text(t)
            elif ev == 'end':
                # The closing root tag has no info for us at all.
                if not is_root:
                    value += u"</%s>" % elem.tag
                    if elem.tail is not None:
                        value += convert_text(elem.tail)

        result[name] = value
    return result


def xml2po(file, translations=None):
    """Return the Android string resource in ``file`` as a babel
    .po catalog.

    If given, the Android string resource in ``translations`` will be
    used for the translated values. In this case, the returned value
    is a 2-tuple (catalog, unmatched), with the latter being a list of
    Android string resource names that are in the translated file, but
    not in the original.
    """
    original_strings = _load_xml_strings(file)
    trans_strings = _load_xml_strings(translations) if translations else None

    catalog = Catalog()
    for name, org_value in original_strings.iteritems():
        trans_value = u""
        if trans_strings:
            trans_value = trans_strings.pop(name, trans_value)

        catalog.add(org_value, trans_value, context=name)
        # Would it be too much to ask for add() to return the message?
        # TODO: Bring this back when we can ensure it won't be added
        # during export/update() either.
        #catalog.get(org_value, context=name).flags.discard('python-format')

    if trans_strings is not None:
        # At this point, trans_strings only contains those for which
        # no original existed.
        return catalog, trans_strings.keys()
    else:
        return catalog


def po2xml(catalog):
    """Convert the gettext catalog in ``catalog`` to an XML DOM.

    This currently relies entirely in the fact that we can use the context
    of each message to specify the Android resource name (which we need
    to do to handle duplicates, but this is a nice by-product). However
    that also means we cannot handle arbitrary catalogs.

    The latter would in theory be possible by using the original,
    untranslated XML to match up a messages id to a resource name, but
    right now we don't support this (and it's not clear it would be
    necessary, even).
    """
    loose_parser = etree.XMLParser(recover=True)

    root_el = etree.Element('resources')
    for message in catalog:
        if not message.id:
            # This is the header
            continue

        if not message.string:
            # Untranslated.
            continue

        value = message.string

        # PREPROCESS
        # The translations may contain arbitrary XHTML, which we need
        # to inject into the DOM to properly output. That means parsing
        # it first.
        # This will now get really messy, since certain XML entities
        # we have unescaped for the translators convenience, while the
        # tag entities &lt; and &gt; we have not, to differentiate them
        # from actual nested tags. Is there any good way to restore this
        # properly?
        # TODO: In particular, the code below will once we do anything
        # bit more complicated with entities, like &amp;amp;lt;
        value = value.replace('&', '&amp;')
        value = value.replace('&amp;lt;', '&lt;')
        value = value.replace('&amp;gt;', '&gt;')

        # PARSE
        value_to_parse = "<string>%s</string>" % value
        try:
            string_el = etree.fromstring(value_to_parse)
        except etree.XMLSyntaxError:
            string_el = etree.fromstring(value_to_parse, loose_parser)
            print "Error: Translation contains invalid XHTML (for resource %s)" % message.context

        def quote(text):
            """Return ``text`` surrounded by quotes if necessary.
            """
            if text is None:
                return

            # If there is trailing or leading whitespace, even if it's
            # just a single space character, we need quoting.
            needs_quoting = text.strip(WHITESPACE) != text

            # Otherwise, there might be collapsible spaces inside the text.
            if not needs_quoting:
                space_count = 0
                for c in chain(text, [EOF]):
                    if c is not EOF and c in WHITESPACE:
                        space_count += 1
                        if space_count >= 2:
                            needs_quoting = True
                            break
                    else:
                        space_count = 0

            if needs_quoting:
                return '"%s"' % text
            return text

        def escape(text):
            """Escape all the characters we know need to be escaped
            in an Android XML file."""
            if text is None:
                return
            text = text.replace('\\', '\\\\')
            text = text.replace('\n', '\\n')
            text = text.replace('\t', '\\t')
            text = text.replace('\'', '\\\'')
            text = text.replace('"', '\\"')
            return text

        # POSTPROCESS
        for element in string_el.iter():
            # Strictly speaking, we wouldn't want to touch things
            # like the root elements tail, but it doesn't matter here,
            # since they are going to be empty string anyway.
            element.text = quote(escape(element.text))
            element.tail = quote(escape(element.tail))

        string_el.attrib['name'] = message.context
        root_el.append(string_el)
    return root_el