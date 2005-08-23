from bzrlib.selftest import TestCase
from bzrlib.diff import internal_diff
from cStringIO import StringIO
def udiff_lines(old, new):
    output = StringIO()
    internal_diff('old', old, 'new', new, output)
    output.seek(0, 0)
    return output.readlines()

def check_patch(lines):
    assert len(lines) > 1, \
        "Not enough lines for a file header for patch:\n%s" % "".join(lines)
    assert lines[0].startswith ('---'), \
        'No orig line for patch:\n%s' % "".join(lines)
    assert lines[1].startswith ('+++'), \
        'No mod line for patch:\n%s' % "".join(lines)
    assert len(lines) > 2, \
        "No hunks for patch:\n%s" % "".join(lines)
    assert lines[2].startswith('@@'),\
        "No hunk header for patch:\n%s" % "".join(lines)
    assert '@@' in lines[2][2:], \
        "Unterminated hunk header for patch:\n%s" % "".join(lines)

class AddNL(TestCase):
    """
    diff generates a valid diff for patches that add a newline
    """
    def runTest(self):
        lines = udiff_lines(['boo'], ['boo\n'])
        check_patch(lines)
        assert lines[4] == '\\ No newline at end of file\n', \
            "expected no-nl, got %r" % lines[4]


class AddNL2(TestCase):
    """
    diff generates a valid diff for patches that change last line and add a
    newline
    """
    def runTest(self):
        lines = udiff_lines(['boo'], ['goo\n'])
        check_patch(lines)
        assert lines[4] == '\\ No newline at end of file\n', \
            "expected no-nl, got %r" % lines[4]

class RemoveNL(TestCase):
    """
    diff generates a valid diff for patches that change last line and add a
    newline
    """
    def runTest(self):
        lines = udiff_lines(['boo\n'], ['boo'])
        check_patch(lines)
        assert lines[5] == '\\ No newline at end of file\n', \
            "expected no-nl, got %r" % lines[5]

TEST_CLASSES = [
    AddNL, 
    AddNL2, 
    RemoveNL,
]
