# Copyright (C) 2005, 2006, 2007 Canonical Ltd
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 59 Temple Place, Suite 330, Boston, MA  02111-1307  USA

# A relatively simple Makefile to assist in building parts of bzr. Mostly for
# building documentation, etc.

.PHONY: all clean extensions pyflakes api-docs

all: extensions

extensions:
	@echo "building extension modules."
	python setup.py build_ext -i

check: docs extensions
	python -Werror ./bzr selftest -v $(tests)
	@echo "Running all tests with no locale."
	LC_CTYPE= LANG=C LC_ALL= ./bzr selftest -v $(tests)
	python -O -Werror ./bzr selftest -v $(tests)

# Run Python style checker (apt-get install pyflakes)
#
# Note that at present this gives many false warnings, because it doesn't
# know about identifiers loaded through lazy_import.
pyflakes:
	pyflakes bzrlib

pyflakes-nounused:
	# There are many of these warnings at the moment and they're not a
	# high priority to fix
	pyflakes bzrlib | grep -v ' imported but unused'

clean:
	python setup.py clean
	-find . -name "*.pyc" -o -name "*.pyo" | xargs rm -f
	rm -rf test????.tmp

docfiles = bzr bzrlib
api-docs:
	mkdir -p api/html
	PYTHONPATH=$(PWD) python tools/bzr_epydoc --html -o api/html --docformat 'restructuredtext en' $(docfiles)

check-api-docs:
	PYTHONPATH=$(PWD) python tools/bzr_epydoc --check --docformat 'restructuredtext en' $(docfiles)

# Produce HTML docs to upload on Canonical server
HTMLDIR := html_docs
PRETTYDIR := pretty_docs

html-docs: docs
	python tools/win32/ostools.py copytodir $(htm_files) doc/default.css $(HTMLDIR)


# translate txt docs to html
doc_dir := doc 
txt_files := $(wildcard $(addsuffix /*.txt, $(doc_dir))) doc/bzr_man.txt
htm_files := $(patsubst %.txt, %.htm, $(txt_files)) 
dev_txt_files := $(wildcard $(addsuffix /*.txt, doc/developers))
dev_htm_files := $(patsubst %.txt, %.htm, $(dev_txt_files)) 

pretty-html-docs: pretty_files

pretty_docs:
	python -c "import os; os.mkdir('$(PRETTYDIR)')"

pretty_files: $(patsubst doc/%.txt, $(PRETTYDIR)/%.htm, $(txt_files))

doc/developers/%.htm: doc/developers/%.txt
	python tools/rst2html.py --link-stylesheet --stylesheet=../default.css --footnote-references=superscript $< $@

doc/developers/HACKING.htm: doc/developers/HACKING
	python tools/rst2html.py --link-stylesheet --stylesheet=../default.css --footnote-references=superscript $< $@

%.htm: %.txt
	python tools/rst2html.py --link-stylesheet --stylesheet=default.css --footnote-references=superscript $< $@

$(PRETTYDIR)/%.htm: pretty_docs doc/%.txt
	python tools/rst2prettyhtml.py doc/bazaar-vcs.org.kid doc/$*.txt \
	$(PRETTYDIR)/$*.htm

MAN_DEPENDENCIES = bzrlib/builtins.py \
		 bzrlib/bundle/commands.py \
		 bzrlib/conflicts.py \
		 bzrlib/sign_my_commits.py \
		 generate_docs.py \
		 tools/doc_generate/__init__.py \
		 tools/doc_generate/autodoc_rstx.py

doc/bzr_man.txt: $(MAN_DEPENDENCIES)
	python generate_docs.py -o $@ rstx

MAN_PAGES = man1/bzr.1
man1/bzr.1: $(MAN_DEPENDENCIES)
	python generate_docs.py -o $@ man

ALL_DOCS = $(htm_files) $(MAN_PAGES) doc/developers/HACKING.htm $(dev_htm_files) doc/developers/performance.png
docs: $(ALL_DOCS)

copy-docs: docs
	python tools/win32/ostools.py copytodir $(htm_files) \
		doc/default.css NEWS README \
		win32_bzr.exe/doc
	python tools/win32/ostools.py copytodir doc/developers/HACKING.htm \
		$(dev_htm_files) \
		win32_bzr.exe/doc/developers

# clean produced docs
clean-docs:
	python tools/win32/ostools.py remove $(ALL_DOCS) \
	$(HTMLDIR) $(PRETTYDIR) doc/bzr_man.txt doc/developers/performance.png


# build a png of our performance task list
doc/developers/performance.png: doc/developers/performance.dot
	@echo Generating $@
	@dot -Tpng $< -o$@ || echo "Dot not installed; skipping generation of $@"


# make bzr.exe for win32 with py2exe
exe:
	@echo *** Make bzr.exe
	python setup.py build_ext -i -f
	python setup.py py2exe > py2exe.log
	python tools/win32/ostools.py copytodir tools/win32/start_bzr.bat win32_bzr.exe
	python tools/win32/ostools.py copytodir tools/win32/bazaar.url win32_bzr.exe

# win32 installer for bzr.exe
installer: exe copy-docs
	@echo *** Make windows installer
	cog.py -d -o tools/win32/bzr.iss tools/win32/bzr.iss.cog
	iscc /Q tools/win32/bzr.iss

# win32 python's distutils-based installer
# require to have python interpreter installed on win32
python-installer: docs
	python24 setup.py bdist_wininst --install-script="bzr-win32-bdist-postinstall.py" -d .
	python25 setup.py bdist_wininst --install-script="bzr-win32-bdist-postinstall.py" -d .


# clean on win32 all installer-related files and directories
clean-win32:
	python tools/win32/ostools.py remove build
	python tools/win32/ostools.py remove win32_bzr.exe
	python tools/win32/ostools.py remove py2exe.log
	python tools/win32/ostools.py remove doc/*.htm
	python tools/win32/ostools.py remove doc/developers/*.htm
	python tools/win32/ostools.py remove doc/bzr_man.txt
	python tools/win32/ostools.py remove tools/win32/bzr.iss
	python tools/win32/ostools.py remove bzr-setup*.exe
	python tools/win32/ostools.py remove bzr-*win32.exe
	python tools/win32/ostools.py remove dist
