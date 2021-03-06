# A general purpose MFC CCtrlView view that uses Scintilla.

import control
import IDLEenvironment # IDLE emulation.
from pywin.mfc import docview
from pywin.mfc import dialog
from scintillacon import *
import win32con
import win32ui
import afxres
import string
import array
import sys
import types
import __main__ # for attribute lookup
import bindings
import keycodes
import regex
import struct
import re
import os

from pywin import is_platform_unicode

PRINTDLGORD = 1538
IDC_PRINT_MAG_EDIT = 1010
EM_FORMATRANGE = win32con.WM_USER+57

wordbreaks = "._" + string.uppercase + string.lowercase + string.digits

patImport=regex.symcomp('import \(<name>.*\)')

_event_commands = [
	# File menu
	"win32ui.ID_FILE_LOCATE", "win32ui.ID_FILE_CHECK", "afxres.ID_FILE_CLOSE",
	"afxres.ID_FILE_NEW", "afxres.ID_FILE_OPEN", "afxres.ID_FILE_SAVE",
	"afxres.ID_FILE_SAVE_AS", "win32ui.ID_FILE_SAVE_ALL",
	# Edit menu
	"afxres.ID_EDIT_UNDO", "afxres.ID_EDIT_REDO", "afxres.ID_EDIT_CUT",
	"afxres.ID_EDIT_COPY", "afxres.ID_EDIT_PASTE", "afxres.ID_EDIT_SELECT_ALL",
	"afxres.ID_EDIT_FIND", "afxres.ID_EDIT_REPEAT", "afxres.ID_EDIT_REPLACE", 
	# View menu
	"win32ui.ID_VIEW_WHITESPACE", "win32ui.ID_VIEW_FIXED_FONT",
	"win32ui.ID_VIEW_BROWSE", "win32ui.ID_VIEW_INTERACTIVE",
	# Window menu
	"afxres.ID_WINDOW_ARRANGE", "afxres.ID_WINDOW_CASCADE",
	"afxres.ID_WINDOW_NEW", "afxres.ID_WINDOW_SPLIT",
	"afxres.ID_WINDOW_TILE_HORZ", "afxres.ID_WINDOW_TILE_VERT",
	# Others
	"afxres.ID_APP_EXIT", "afxres.ID_APP_ABOUT",
]

_extra_event_commands = [
	("EditDelete", afxres.ID_EDIT_CLEAR),
	("LocateModule", win32ui.ID_FILE_LOCATE),
	("GotoLine", win32ui.ID_EDIT_GOTO_LINE),
	("DbgBreakpointToggle", win32ui.IDC_DBG_ADD),
	("DbgGo", win32ui.IDC_DBG_GO),
	("DbgStepOver", win32ui.IDC_DBG_STEPOVER),
	("DbgStep", win32ui.IDC_DBG_STEP),
	("DbgStepOut", win32ui.IDC_DBG_STEPOUT),
	("DbgBreakpointClearAll", win32ui.IDC_DBG_CLEAR),
	("DbgClose", win32ui.IDC_DBG_CLOSE),
]

event_commands = []
def _CreateEvents():
	for name in _event_commands:
		val = eval(name)
		name_parts = string.split(name, "_")[1:]
		name_parts = map(string.capitalize, name_parts)
		event  =string.join(name_parts,'')
		event_commands.append((event, val))
	for name, id in _extra_event_commands:
		event_commands.append((name, id))

_CreateEvents()
del _event_commands; del _extra_event_commands

command_reflectors = [
	(win32ui.ID_EDIT_UNDO, win32con.WM_UNDO),
	(win32ui.ID_EDIT_REDO, SCI_REDO),
	(win32ui.ID_EDIT_CUT, win32con.WM_CUT),
	(win32ui.ID_EDIT_COPY, win32con.WM_COPY),
	(win32ui.ID_EDIT_PASTE, win32con.WM_PASTE),
	(win32ui.ID_EDIT_CLEAR, win32con.WM_CLEAR),
	(win32ui.ID_EDIT_SELECT_ALL, SCI_SELECTALL),
]

def DoBraceMatch(control):
		curPos = control.SCIGetCurrentPos()
		charBefore = ' '
		if curPos: charBefore = control.SCIGetCharAt(curPos-1)
		charAt = control.SCIGetCharAt(curPos)
		braceAtPos = braceOpposite = -1
		if charBefore in "[](){}": braceAtPos = curPos-1
		if braceAtPos==-1:
			if charAt in "[](){}": braceAtPos = curPos
		if braceAtPos != -1:
			braceOpposite = control.SCIBraceMatch(braceAtPos, 0)
		if braceAtPos != -1 and braceOpposite==-1:
			control.SCIBraceBadHighlight(braceAtPos)
		else:
			# either clear them both or set them both.
			control.SCIBraceHighlight(braceAtPos, braceOpposite)

def _get_class_attributes(ob):
	# Recurse into base classes looking for attributes
	items = []
	try:
		items = items + dir(ob)
		for i in ob.__bases__:
			for item in _get_class_attributes(i):
				if item not in items:
					items.append(item)
	except AttributeError:
		pass
	return items

# Supposed to look like an MFC CEditView, but 
# also supports IDLE extensions and other source code generic features.
class CScintillaView(docview.CtrlView, control.CScintillaColorEditInterface):
	def __init__(self, doc):
		docview.CtrlView.__init__(self, doc, "Scintilla", win32con.WS_CHILD | win32con.WS_VSCROLL | win32con.WS_HSCROLL | win32con.WS_CLIPCHILDREN | win32con.WS_VISIBLE)
		self._tabWidth = 8 # Mirror of what we send to Scintilla - never change this directly
		self.bAutoCompleteAttributes = 1
		self.bShowCallTips = 1
		self.bMatchBraces = 0 # Editor option will default this to true later!
		self.bindings = bindings.BindingsManager(self)

		self.idle = IDLEenvironment.IDLEEditorWindow(self)
		self.idle.IDLEExtension("AutoExpand")
		# SendScintilla is called so frequently it is worth optimizing.
		self.SendScintilla = self._obj_.SendMessage

	def OnDestroy(self, msg):
		self.SendScintilla = None
		return docview.CtrlView.OnDestroy(self, msg)

	def _MakeColorizer(self):
		ext = os.path.splitext(self.GetDocument().GetPathName())[1]
		import formatter
		return formatter.BuiltinPythonSourceFormatter(self, ext)

	
#	def SendScintilla(self, msg, w=0, l=0):
#		return self._obj_.SendMessage(msg, w, l)

	def SCISetTabWidth(self, width):
		# I need to remember the tab-width for the AutoIndent extension.  This may go.
		self._tabWidth = width
		control.CScintillaEditInterface.SCISetTabWidth(self, width)

	def GetTabWidth(self):
		return self._tabWidth

	def HookHandlers(self):
		# Create events for all the menu names.
		for name, val in event_commands:
#			handler = lambda id, code, tosend=val, parent=parent: parent.OnCommand(tosend, 0) and 0
			self.bindings.bind(name, None, cid=val)

		# Hook commands that do nothing other than send Scintilla messages.
		for command, reflection in command_reflectors:
			handler = lambda id, code, ss=self.SendScintilla, tosend=reflection: ss(tosend) and 0
			self.HookCommand(handler, command)

		self.HookCommand(self.OnCmdViewWS, win32ui.ID_VIEW_WHITESPACE)
		self.HookCommandUpdate(self.OnUpdateViewWS, win32ui.ID_VIEW_WHITESPACE)
		self.HookCommand(self.OnCmdViewIndentationGuides, win32ui.ID_VIEW_INDENTATIONGUIDES)
		self.HookCommandUpdate(self.OnUpdateViewIndentationGuides, win32ui.ID_VIEW_INDENTATIONGUIDES)
		self.HookCommand(self.OnCmdViewRightEdge, win32ui.ID_VIEW_RIGHT_EDGE)
		self.HookCommandUpdate(self.OnUpdateViewRightEdge, win32ui.ID_VIEW_RIGHT_EDGE)
		self.HookCommand(self.OnCmdViewEOL, win32ui.ID_VIEW_EOL)
		self.HookCommandUpdate(self.OnUpdateViewEOL, win32ui.ID_VIEW_EOL)
		self.HookCommand(self.OnCmdViewFixedFont, win32ui.ID_VIEW_FIXED_FONT)
		self.HookCommandUpdate(self.OnUpdateViewFixedFont, win32ui.ID_VIEW_FIXED_FONT)
		self.HookCommand(self.OnCmdFileLocate, win32ui.ID_FILE_LOCATE)
		self.HookCommand(self.OnCmdEditFind, win32ui.ID_EDIT_FIND)
		self.HookCommand(self.OnCmdEditRepeat, win32ui.ID_EDIT_REPEAT)
		self.HookCommand(self.OnCmdEditReplace, win32ui.ID_EDIT_REPLACE)
		self.HookCommand(self.OnCmdGotoLine, win32ui.ID_EDIT_GOTO_LINE)
		self.HookCommand(self.OnFilePrint, afxres.ID_FILE_PRINT)
		self.HookCommand(self.OnFilePrint, afxres.ID_FILE_PRINT_DIRECT)
		self.HookCommand(self.OnFilePrintPreview,
			win32ui.ID_FILE_PRINT_PREVIEW)
		# Key bindings.
		self.HookMessage(self.OnKeyDown, win32con.WM_KEYDOWN)
		self.HookMessage(self.OnKeyDown, win32con.WM_SYSKEYDOWN)
		# Hook wheeley mouse events
#		self.HookMessage(self.OnMouseWheel, win32con.WM_MOUSEWHEEL)
		self.HookFormatter()

	def OnInitialUpdate(self):
		doc = self.GetDocument()

		# Enable Unicode if we can
		if is_platform_unicode:
			self.SendScintilla(SCI_SETCODEPAGE, SC_CP_UTF8, 0)
		# Create margins
		self.SendScintilla(SCI_SETMARGINTYPEN, 1, SC_MARGIN_SYMBOL);
		self.SendScintilla(SCI_SETMARGINMASKN, 1, 0xF);
		self.SendScintilla(SCI_SETMARGINTYPEN, 2, SC_MARGIN_SYMBOL);
		self.SendScintilla(SCI_SETMARGINMASKN, 2, SC_MASK_FOLDERS);
		self.SendScintilla(SCI_SETMARGINSENSITIVEN, 2, 1);

		self.GetDocument().HookViewNotifications(self) # is there an MFC way to grab this?
		self.HookHandlers()

		# Load the configuration information.
		self.OnWinIniChange(None)

		self.SetSel()

		self.GetDocument().FinalizeViewCreation(self) # is there an MFC way to grab this?
		

	def _GetSubConfigNames(self):
		return None # By default we use only sections without sub-sections.

	def OnWinIniChange(self, section = None):
		self.bindings.prepare_configure()
		try:
			self.DoConfigChange()
		finally:
			self.bindings.complete_configure()

	def DoConfigChange(self):
		# Bit of a hack I dont kow what to do about - these should be "editor options"
		from pywin.framework.editor import GetEditorOption
		self.bAutoCompleteAttributes = GetEditorOption("Autocomplete Attributes", 1)
		self.bShowCallTips = GetEditorOption("Show Call Tips", 1)
		# Update the key map and extension data.
		configManager.configure(self, self._GetSubConfigNames())
		if configManager.last_error:
			win32ui.MessageBox(configManager.last_error, "Configuration Error")
		self.bMatchBraces = GetEditorOption("Match Braces", 1)
		self.ApplyFormattingStyles(1)

	def OnDestroy(self, msg):
		self.bindings.close()
		self.bindings = None
		self.idle.close()
		self.idle = None
		control.CScintillaColorEditInterface.close(self)
		return docview.CtrlView.OnDestroy(self, msg)

	def OnMouseWheel(self, msg):
		zDelta = msg[2] >> 16
		vpos = self.GetScrollPos(win32con.SB_VERT)
		vpos = vpos - zDelta/40 # 3 lines per notch
		self.SetScrollPos(win32con.SB_VERT, vpos)
		self.SendScintilla(win32con.WM_VSCROLL,
						   (vpos<<16) | win32con.SB_THUMBPOSITION,
						   0)

	def OnBraceMatch(self, std, extra):
		if not self.bMatchBraces: return
		DoBraceMatch(self)

	def OnNeedShown(self, std, extra):
		notify = self.SCIUnpackNotifyMessage(extra)
		# OnNeedShown is called before an edit operation when
		# text is folded (as it is possible the text insertion will happen
		# in a folded region.)  As this happens _before_ the insert,
		# we ignore the length (if we are at EOF, pos + length may
		# actually be beyond the end of buffer)
		self.EnsureCharsVisible(notify.position)

	def EnsureCharsVisible(self, start, end = None):
		if end is None: end = start
		lineStart = self.LineFromChar(min(start, end))
		lineEnd = self.LineFromChar(max(start, end))
		while lineStart <= lineEnd:
			self.SCIEnsureVisible(lineStart)
			lineStart = lineStart + 1

	# Helper to add an event to a menu.
	def AppendMenu(self, menu, text="", event=None, flags = None, checked=0):
		if event is None:
			assert flags is not None, "No event or custom flags!"
			cmdid = 0
		else:
			cmdid = self.bindings.get_command_id(event)
			if cmdid is None:
				# No event of that name - no point displaying it.
				print 'View.AppendMenu(): Unknown event "%s" specified for menu text "%s" - ignored' % (event, text)
				return 
			keyname = configManager.get_key_binding( event, self._GetSubConfigNames() )
			if keyname is not None:
				text = text + "\t" + keyname
		if flags is None: flags = win32con.MF_STRING|win32con.MF_ENABLED
		if checked: flags = flags | win32con.MF_CHECKED
		menu.AppendMenu(flags, cmdid, text)

	def OnKeyDown(self, msg):
		return self.bindings.fire_key_event( msg )

	def GotoEndOfFileEvent(self, event):
		self.SetSel(-1)

	def KeyDotEvent(self, event):
		self.SCIAddText(".")
		if self.bAutoCompleteAttributes:
			self._AutoComplete()

	# View Whitespace/EOL/Indentation UI.

	def OnCmdViewWS(self, cmd, code): # Handle the menu command
		viewWS = self.SCIGetViewWS()
		self.SCISetViewWS(not viewWS)
	def OnUpdateViewWS(self, cmdui): # Update the tick on the UI.
		cmdui.SetCheck(self.SCIGetViewWS())
		cmdui.Enable()
	def OnCmdViewIndentationGuides(self, cmd, code): # Handle the menu command
		viewIG = self.SCIGetIndentationGuides()
		self.SCISetIndentationGuides(not viewIG)
	def OnUpdateViewIndentationGuides(self, cmdui): # Update the tick on the UI.
		cmdui.SetCheck(self.SCIGetIndentationGuides())
		cmdui.Enable()
	def OnCmdViewRightEdge(self, cmd, code): # Handle the menu command
		if self.SCIGetEdgeMode() == EDGE_NONE:
			mode = EDGE_BACKGROUND
		else:
			mode = EDGE_NONE
		self.SCISetEdgeMode(mode)
	def OnUpdateViewRightEdge(self, cmdui): # Update the tick on the UI.
		cmdui.SetCheck(self.SCIGetEdgeMode() != EDGE_NONE)
		cmdui.Enable()
	def OnCmdViewEOL(self, cmd, code): # Handle the menu command
		viewEOL = self.SCIGetViewEOL()
		self.SCISetViewEOL(not viewEOL)
	def OnUpdateViewEOL(self, cmdui): # Update the tick on the UI.
		cmdui.SetCheck(self.SCIGetViewEOL())
		cmdui.Enable()

	def OnCmdViewFixedFont(self, cmd, code): # Handle the menu command
		self._GetColorizer().bUseFixed = not self._GetColorizer().bUseFixed
		self.ApplyFormattingStyles(0)
		# Ensure the selection is visible!
		self.ScrollCaret()

	def OnUpdateViewFixedFont(self, cmdui): # Update the tick on the UI.
		c = self._GetColorizer()
		if c is not None: cmdui.SetCheck(c.bUseFixed)
		cmdui.Enable(c is not None)

	def OnCmdEditFind(self, cmd, code):
		import find
		find.ShowFindDialog()
	def OnCmdEditRepeat(self, cmd, code):
		import find
		find.FindNext()
	def OnCmdEditReplace(self, cmd, code):
		import find
		find.ShowReplaceDialog()

	def OnCmdFileLocate(self, cmd, id):
		line=string.strip(self.GetLine())
		import pywin.framework.scriptutils
		if patImport.match(line)==len(line):
			# Module name on this line - locate that!
			modName = patImport.group('name')
			fileName = pywin.framework.scriptutils.LocatePythonFile(modName)
			if fileName is None:
				win32ui.SetStatusText("Can't locate module %s" % modName)
				return 1 # Let the default get it.
			else:
				win32ui.GetApp().OpenDocumentFile(fileName)
		else:
			# Just to a "normal" locate - let the default handler get it.
			return 1
		return 0

	def OnCmdGotoLine(self, cmd, id):
		try:
			lineNo = string.atoi(raw_input("Enter Line Number"))-1
		except (ValueError, KeyboardInterrupt):
			return 0
		self.SCIEnsureVisible(lineNo)
		self.SCIGotoLine(lineNo)
		return 0

	def SaveTextFile(self, filename):
		doc = self.GetDocument()
		s = self.GetTextRange()
		if is_platform_unicode:
			s = unicode(s,"utf-8").encode("mbcs")
		f  = open(filename, 'wb')
		f.write(s)
		f.close()
		doc.SetModifiedFlag(0)
		return 1

	def _AutoComplete(self):
		self.SCIAutoCCancel() # Cancel old auto-complete lists.
		# First try and get an object without evaluating calls
		ob = self._GetObjectAtPos(bAllowCalls = 0)
		# If that failed, try and process call or indexing to get the object.
		if ob is None:
			ob = self._GetObjectAtPos(bAllowCalls = 1)
		if ob is not None:
			items = []
			try: # Catch unexpected errors when fetching attribute names from the object
				try:
					items = items + dir(ob)
				except AttributeError:
					pass # object has no __dict__
				if hasattr(ob, "__class__"):
					items = items + _get_class_attributes(ob.__class__)
				# All names that start with "_" go!
				items = filter(lambda word: word[0]!='_', items)
				# The object may be a COM object with typelib support - lets see if we can get its props.
				# (contributed by Stefan Migowsky)
				try:
					# Get the automation attributes
					list = ob.__class__._prop_map_get_.keys()
					# See if there is an write only property 
					# could be optimized
					for i in ob.__class__._prop_map_put_.keys():
						if i not in list:
							list.append(i)
					# append to the already evaluated list
					items = items + list
				except AttributeError:
					pass
			except:
				win32ui.SetStatusText("Error attempting to get object attributes - %s" % (`sys.exc_info()[0]`,))


			if items:
				self.SCIAutoCShow(items)
		else:
			# Heuristics a-la AutoExpand
			# The idea is to find other usages of the current binding
			# and assume, that it refers to the same object (or at least,
			# to an object of the same type)
			# Contributed by Vadim Chugunov [vadimch@yahoo.com]
			left, right = self._GetWordSplit()
			if left=="": # Ignore standalone dots
				return None
			# We limit our search to the current class, if that
			# information is available
			minline, maxline, curclass = self._GetClassInfoFromBrowser()
			endpos = self.LineIndex(maxline)
			text = self.GetTextRange(self.LineIndex(minline),endpos)
			try:
				list = re.findall(r"\b"+left+"\.\w+",text)
			except re.error:
				# parens etc may make an invalid RE, but this code wouldnt
				# benefit even if the RE did work :-)
				list = []
			prefix = len(left)+1
			unique = {}
			for li in list:
				unique[li[prefix:]] = 1
			# Assuming traditional usage of self...
			if curclass and left=="self":
				self._UpdateWithClassMethods(unique,curclass)

			items = filter(lambda word: word[:2]!='__' or word[-2:]!='__', unique.keys())
			# Ignore the word currently to the right of the dot - probably a red-herring.
			try:
				items.remove(right[1:])
			except ValueError:
				pass
			if items:
				self.SCIAutoCShow(items)

	# TODO: This is kinda slow. Probably need some kind of cache 
	# here that is flushed upon file save
	# Or maybe we don't need the superclass methods at all ?
	def _UpdateWithClassMethods(self,dict,classinfo):
		dict.update(classinfo.methods)
		for super in classinfo.super:
			if hasattr(super,"methods"):
				self._UpdateWithClassMethods(dict,super)

	# Find which class definition caret is currently in and return
	# indexes of the the first and the last lines of that class definition
	# Data is obtained from module browser (if enabled)
	def _GetClassInfoFromBrowser(self,pos=-1):
		minline = 0
		maxline = self.GetLineCount()-1
		doc = self.GetParentFrame().GetActiveDocument()
		browser = None
		try:
			if doc is not None:
				browser = doc.GetAllViews()[1]
		except IndexError:
			pass
		if browser is None:
			return (minline,maxline,None) # Current window has no browser
		if not browser.list: return (minline,maxline,None) # Not initialized
		path = self.GetDocument().GetPathName()
		if not path: return (minline,maxline,None) # No current path
		
		import pywin.framework.scriptutils
		curmodule, path = pywin.framework.scriptutils.GetPackageModuleName(path)
		try:
			clbrdata = browser.list.root.clbrdata
		except AttributeError:
			return (minline,maxline,None) # No class data for this module.
		curline = self.LineFromChar(pos)
		curclass = None
		# Find out which class we are in
		for item in clbrdata.values():
			if item.module==curmodule:
				item_lineno = item.lineno - 1 # Scintilla counts lines from 0, whereas pyclbr - from 1
				if minline < item_lineno <= curline:
					minline = item_lineno
					curclass = item
				if curline < item_lineno < maxline:
					maxline = item_lineno
		return (minline,maxline,curclass)


	def _GetObjectAtPos(self, pos = -1, bAllowCalls = 0):
		left, right = self._GetWordSplit(pos, bAllowCalls)
		if left: # It is an attribute lookup
			# How is this for a hack!
			namespace = sys.modules.copy()
			namespace.update(__main__.__dict__)
			# Get the debugger's context.
			try:
				from pywin.framework import interact
				if interact.edit is not None and interact.edit.currentView is not None:
					globs, locs = interact.edit.currentView.GetContext()[:2]
					if globs: namespace.update(globs)
					if locs: namespace.update(locs)
			except ImportError:
				pass
			try:
				return eval(left, namespace)
			except:
				pass
		return None

	def _GetWordSplit(self, pos = -1, bAllowCalls = 0):
		if pos==-1: pos = self.GetSel()[0]-1 # Character before current one
		limit = self.GetTextLength()
		before = []
		after = []
		index = pos-1
		wordbreaks_use = wordbreaks
		if bAllowCalls: wordbreaks_use = wordbreaks_use + "()[]"
		while index>=0:
			char = self.SCIGetCharAt(index)
			if char not in wordbreaks_use: break
			before.insert(0, char)
			index = index-1
		index = pos
		while index<=limit:
			char = self.SCIGetCharAt(index)
			if char not in wordbreaks_use: break
			after.append(char)
			index=index+1
		return string.join(before,''), string.join(after,'')

	def OnPrepareDC (self, dc, pInfo):
#		print "OnPrepareDC for page", pInfo.GetCurPage(), "of", pInfo.GetFromPage(), "to", pInfo.GetToPage(), ", starts=", self.starts
		if dc.IsPrinting():
			# Check if we are beyond the end.
			# (only do this when actually printing, else messes up print preview!)
			if not pInfo.GetPreview() and self.starts is not None:
				prevPage = pInfo.GetCurPage() - 1
				if prevPage > 0 and self.starts[prevPage] >= self.GetTextLength():
					# All finished.
					pInfo.SetContinuePrinting(0)
					return
			dc.SetMapMode(win32con.MM_TEXT);

	def OnPreparePrinting(self, pInfo):
		flags = win32ui.PD_USEDEVMODECOPIES | \
		        win32ui.PD_ALLPAGES | \
		        win32ui.PD_NOSELECTION # Dont support printing just a selection.
# NOTE: Custom print dialogs are stopping the user's values from coming back :-(
#		self.prtDlg = PrintDialog(pInfo, PRINTDLGORD, flags)
#		pInfo.SetPrintDialog(self.prtDlg)
		pInfo.SetMinPage(1)
		# max page remains undefined for now.
		pInfo.SetFromPage(1)
		pInfo.SetToPage(1)
		ret = self.DoPreparePrinting(pInfo)
		return ret

	def OnBeginPrinting(self, dc, pInfo):
		self.starts = None
		return self._obj_.OnBeginPrinting(dc, pInfo)

	def CalculatePageRanges(self, dc, pInfo):
		# Calculate page ranges and max page
		self.starts = {0:0}
		metrics = dc.GetTextMetrics()
		left, top, right, bottom = pInfo.GetDraw()
		# Leave space at the top for the header.
		rc = (left, top + (9*metrics['tmHeight'])/2, right, bottom)
		pageStart = 0
		maxPage = 0
		textLen = self.GetTextLength()
		while pageStart < textLen:
			pageStart = self.FormatRange(dc, pageStart, textLen, rc, 0)
			maxPage = maxPage + 1
			self.starts[maxPage] = pageStart
		# And a sentinal for one page past the end
		self.starts[maxPage+1] = textLen
		# When actually printing, maxPage doesnt have any effect at this late state.
		# but is needed to make the Print Preview work correctly.
		pInfo.SetMaxPage(maxPage)

	def OnFilePrintPreview(self, *arg):
		self._obj_.OnFilePrintPreview()

	def OnFilePrint(self, *arg):
		self._obj_.OnFilePrint()

	def FormatRange(self, dc, pageStart, lengthDoc, rc, draw):
		hdcRender = dc.GetHandleOutput()
		hdcFormat = dc.GetHandleAttrib()
		fr = struct.pack('LLIIIIIIIIll', hdcRender, hdcFormat, rc[0], rc[1], rc[2], rc[3], rc[0], rc[1], rc[2], rc[3], pageStart, lengthDoc)
		frBuff = array.array('c', fr)
		addressFrBuff = frBuff.buffer_info()[0]
		nextPageStart = self.SendScintilla(EM_FORMATRANGE, draw, addressFrBuff)
		return nextPageStart

	def OnPrint(self, dc, pInfo):
		metrics = dc.GetTextMetrics()
#		print "dev", w, h, l, metrics['tmAscent'], metrics['tmDescent']
		if self.starts is None:
			self.CalculatePageRanges(dc, pInfo)
		pageNum = pInfo.GetCurPage() - 1
		# Setup the header of the page - docname on left, pagenum on right.
		doc = self.GetDocument()
		cxChar = metrics['tmAveCharWidth']
		cyChar = metrics['tmHeight']
		left, top, right, bottom = pInfo.GetDraw()
		dc.TextOut(0, 2*cyChar, doc.GetTitle())
		pagenum_str = win32ui.LoadString(afxres.AFX_IDS_PRINTPAGENUM) % (pageNum+1,)
		dc.SetTextAlign(win32con.TA_RIGHT)
		dc.TextOut(right, 2*cyChar, pagenum_str)
		dc.SetTextAlign(win32con.TA_LEFT)
		top = top + (7*cyChar)/2
		dc.MoveTo(left, top)
		dc.LineTo(right, top)
		top = top + cyChar
		rc = (left, top, right, bottom)
		nextPageStart = self.FormatRange(dc, self.starts[pageNum], self.starts[pageNum+1], rc, 1)

def LoadConfiguration():
	global configManager
	# Bit of a hack I dont kow what to do about?
	from config import ConfigManager
	configName = rc = win32ui.GetProfileVal("Editor", "Keyboard Config", "default")
	configManager = ConfigManager(configName)
	if configManager.last_error:
		bTryDefault = 0
		msg = "Error loading configuration '%s'\n\n%s" % (configName, configManager.last_error)
		if configName != "default":
			msg = msg + "\n\nThe default configuration will be loaded."
			bTryDefault = 1
		win32ui.MessageBox(msg)
		if bTryDefault:
			configManager = ConfigManager("default")
			if configManager.last_error:
				win32ui.MessageBox("Error loading configuration 'default'\n\n%s" % (configManager.last_error))

configManager = None
LoadConfiguration()
