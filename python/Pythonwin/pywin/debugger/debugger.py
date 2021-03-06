# debugger.py

# A debugger for Pythonwin.  Built from pdb.

# Mark Hammond (MHammond@skippinet.com.au) - Dec 94.

# usage:
# >>> import pywin.debugger
# >>> pywin.debugger.GetDebugger().run("command")

import pdb
import bdb
import sys
import string
import os
import regsub
import types

import win32ui
import win32api
import win32con
import pywin.docking.DockingBar
from pywin.mfc import dialog, object, afxres, window
from pywin.framework import app, interact, editor, scriptutils
from pywin.framework.editor.color.coloreditor import MARKER_CURRENT, MARKER_BREAKPOINT
from pywin.tools import browser, hierlist
import commctrl
import traceback

#import win32traceutil

from dbgcon import *

error = "pywin.debugger.error"

def SetInteractiveContext(globs, locs):
	if interact.edit is not None and interact.edit.currentView is not None:
		interact.edit.currentView.SetContext(globs, locs)

def _LineStateToMarker(ls):
	if ls==LINESTATE_CURRENT:
		return MARKER_CURRENT
#	elif ls == LINESTATE_CALLSTACK:
#		return MARKER_CALLSTACK
	return MARKER_BREAKPOINT

class HierListItem(browser.HLIPythonObject):
	pass

class HierFrameItem(HierListItem):
	def __init__(self, frame, debugger):
		HierListItem.__init__(self, frame, None)
		self.debugger = debugger
	def GetText(self):
		name = self.myobject.f_code.co_name
		if not name or name == '?' :
			# See if locals has a '__name__' (ie, a module)
			if self.myobject.f_locals.has_key('__name__'):
				name = self.myobject.f_locals['__name__'] + " module"
			else:
				name = '<Debugger Context>'

		return "%s   (%s:%d)" % (name, os.path.split(self.myobject.f_code.co_filename)[1], self.myobject.f_lineno)
	def GetBitmapColumn(self):
		if self.debugger.curframe is self.myobject:
			return 7
		else:
			return 8
	def GetSubList(self):
		ret = []
		ret.append(HierFrameDict(self.myobject.f_locals, "Locals", 2))
		ret.append(HierFrameDict(self.myobject.f_globals, "Globals", 1))
		return ret
	def IsExpandable(self):
		return 1
	def TakeDefaultAction(self):
		# Set the default frame to be this frame.
		self.debugger.set_cur_frame(self.myobject)
		return 1

class HierFrameDict(browser.HLIDict):
	def __init__(self, dict, name, bitmapColumn):
		self.bitmapColumn=bitmapColumn
		browser.HLIDict.__init__(self, dict, name)
	def GetBitmapColumn(self):
		return self.bitmapColumn

class NoStackAvailableItem(HierListItem):
	def __init__(self, why):
		HierListItem.__init__(self, None, why)
	def IsExpandable(self):
		return 0
	def GetText(self):
		return self.name
	def GetBitmapColumn(self):
		return 8

class HierStackRoot(HierListItem):
	def __init__( self, debugger ):
		HierListItem.__init__(self, debugger, None)
		self.last_stack = []
##	def __del__(self):
##		print "HierStackRoot dieing"
	def GetSubList(self):
		debugger = self.myobject
#		print self.debugger.stack, self.debugger.curframe
		ret = []
		if debugger.debuggerState==DBGSTATE_BREAK:
			stackUse=debugger.stack[:]
			stackUse.reverse()
			self.last_stack = []
			for frame, lineno in stackUse:
				self.last_stack.append( (frame, lineno) )
				if frame is debugger.userbotframe: # Dont bother showing frames below our bottom frame.
					break
		for frame, lineno in self.last_stack:
			ret.append( HierFrameItem( frame, debugger ) )
##		elif debugger.debuggerState==DBGSTATE_NOT_DEBUGGING:
##			ret.append(NoStackAvailableItem('<nothing is being debugged>'))
##		else:
##			ret.append(NoStackAvailableItem('<stack not available while running>'))
		return ret
	def GetText(self):
		return 'root item'
	def IsExpandable(self):
		return 1

class HierListDebugger(hierlist.HierListWithItems):
	""" Hier List of stack frames, breakpoints, whatever """
	def __init__(self, debugger):
		self.debugger = debugger
		hierlist.HierListWithItems.__init__(self, None, win32ui.IDB_DEBUGGER_HIER, None, win32api.RGB(255,0,0))
	def Setup(self):
		root = HierStackRoot(self.debugger)
		self.AcceptRoot(root)
#	def Refresh(self):
#		self.Setup()

class DebuggerWindow(window.Wnd):
	def GetDefRect(self):
		defRect = app.LoadWindowSize("Debugger Windows\\" + self.title)
		if defRect[2]-defRect[0]==0:
			defRect = 0, 0, 150, 150
		return defRect

	def OnDestroy(self, msg):
		newSize = self.GetWindowPlacement()[4]
		pywin.framework.app.SaveWindowSize("Debugger Windows\\" + self.title, newSize)
		return window.Wnd.OnDestroy(self, msg)

	def OnKeyDown(self, msg):
		key = msg[2]
		if key in [13, 27, 32]: return 1
		if key in [46,8]: # delete/BS key
			self.DeleteSelected()
			return 0
		view = scriptutils.GetActiveView()
		try:
			firer = view.bindings.fire_key_event
		except AttributeError:
			firer = None
		if firer is not None:
			return firer(msg)
		else:
			return 1

	def DeleteSelected(self):
		win32api.MessageBeep()

	def EditSelected(self):
		win32api.MessageBeep()

class DebuggerStackWindow(DebuggerWindow):
	title = "Stack"
	def __init__(self, debugger):
		DebuggerWindow.__init__(self, win32ui.CreateTreeCtrl())
		self.list = HierListDebugger(debugger)
		self.listOK = 1
	def SaveState(self):
		self.list.DeleteAllItems()
		self.listOK = 0
	def CreateWindow(self, parent):
		style = win32con.WS_CHILD | win32con.WS_VISIBLE | win32con.WS_BORDER | commctrl.TVS_HASLINES | commctrl.TVS_LINESATROOT | commctrl.TVS_HASBUTTONS
		self._obj_.CreateWindow(style, self.GetDefRect(), parent, win32ui.IDC_LIST1)
		self.HookMessage(self.OnKeyDown, win32con.WM_KEYDOWN)
		self.HookMessage(self.OnKeyDown, win32con.WM_SYSKEYDOWN)
		self.list.HierInit (parent, self)
		self.list.Setup()

	def RespondDebuggerState(self, state):
		if not self.listOK:
			self.listOK = 1
			self.list.Setup()
		else:			
			self.list.Refresh()

	def RespondDebuggerData(self):
		try:
			handle = self.GetChildItem(0)
		except win32ui.error:
			return # No items
		while 1:
			item = self.list.ItemFromHandle(handle)
			col = self.list.GetBitmapColumn(item)
			selCol = self.list.GetSelectedBitmapColumn(item)
			if selCol is None: selCol = col
			if self.list.GetItemImage(handle)!= (col, selCol):
				self.list.SetItemImage(handle, col, selCol)
			try:
				handle = self.GetNextSiblingItem(handle)
			except win32ui.error:
				break

class DebuggerListViewWindow(DebuggerWindow):
	def __init__(self, debugger):
		DebuggerWindow.__init__(self, win32ui.CreateListCtrl())
		self.debugger = debugger
	def CreateWindow(self, parent):
		list = self
		style = win32con.WS_CHILD | win32con.WS_VISIBLE | win32con.WS_BORDER | commctrl.LVS_EDITLABELS | commctrl.LVS_REPORT
		self._obj_.CreateWindow(style, self.GetDefRect(), parent, win32ui.IDC_LIST1)
		self.HookMessage(self.OnKeyDown, win32con.WM_KEYDOWN)
		self.HookMessage(self.OnKeyDown, win32con.WM_SYSKEYDOWN)
		list = self
		title, width = self.columns[0]
		itemDetails = (commctrl.LVCFMT_LEFT, width, title, 0)
		list.InsertColumn(0, itemDetails)
		col = 1
		for title, width in self.columns[1:]:
			col = col + 1
			itemDetails = (commctrl.LVCFMT_LEFT, width, title, 0)
			list.InsertColumn(col, itemDetails)
		parent.HookNotify( self.OnListEndLabelEdit, commctrl.LVN_ENDLABELEDIT)
		parent.HookNotify(self.OnItemRightClick, commctrl.NM_RCLICK)
		parent.HookNotify(self.OnItemDoubleClick, commctrl.NM_DBLCLK)

	def RespondDebuggerData(self):
		pass

	def RespondDebuggerState(self, state):
		pass

	def EditSelected(self):
		sel = self.GetNextItem(-1, commctrl.LVNI_SELECTED)
		if sel == -1:
			return
		self.EditLabel(sel)

	def OnKeyDown(self, msg):
		key = msg[2]
		# If someone starts typing, they probably are trying to edit the text!
		if chr(key) in string.uppercase:
			self.EditSelected()
			return 0
		return DebuggerWindow.OnKeyDown(self, msg)

	def OnItemDoubleClick(self, notify_data, extra):
		self.EditSelected()

	def OnItemRightClick(self, notify_data, extra):
		# First select the item we right-clicked on.
		pt = self.ScreenToClient(win32api.GetCursorPos())
		flags, hItem, subitem = self.HitTest(pt)
		if hItem==-1 or commctrl.TVHT_ONITEM & flags==0:
			return None
		self.SetItemState(hItem, commctrl.LVIS_SELECTED, commctrl.LVIS_SELECTED)

		menu = win32ui.CreatePopupMenu()
		menu.AppendMenu(win32con.MF_STRING|win32con.MF_ENABLED,1000, "Edit item")
		menu.AppendMenu(win32con.MF_STRING|win32con.MF_ENABLED,1001, "Delete item")
		dockbar = self.GetParent()
		if dockbar.IsFloating():
			hook_parent = win32ui.GetMainFrame()
		else:
			hook_parent = self.GetParentFrame()
		hook_parent.HookCommand(self.OnEditItem, 1000)
		hook_parent.HookCommand(self.OnDeleteItem, 1001)
		menu.TrackPopupMenu(win32api.GetCursorPos()) # track at mouse position.
		return None

	def OnDeleteItem(self,command, code):
		self.DeleteSelected()
	def OnEditItem(self, command, code):
		self.EditSelected()

class DebuggerBreakpointsWindow(DebuggerListViewWindow):
	title = "Breakpoints"
	columns = [ ("Condition", 70), ("Location", 1024)]

	def SaveState(self):
		pass

	def OnListEndLabelEdit(self, std, extra):
		item = extra[0]
		text = item[4]
		if text is None: return

		item_id = self.GetItem(item[0])[6]

		from bdb import Breakpoint
		for bplist in Breakpoint.bplist.values():
			for bp in bplist:
				if id(bp)==item_id:
					if string.lower(string.strip(text))=="none":
						text = None
					bp.cond = text
					break
		self.RespondDebuggerData()

	def DeleteSelected(self):
		try:
			num = self.GetNextItem(-1, commctrl.LVNI_SELECTED)
			item_id = self.GetItem(num)[6]
			from bdb import Breakpoint
			for bplist in Breakpoint.bplist.values():
				for bp in bplist:
					if id(bp)==item_id:
						self.debugger.clear_break(bp.file, bp.line)
						break
		except win32ui.error:
			win32api.MessageBeep()
		self.RespondDebuggerData()

	def RespondDebuggerData(self):
		list = self
		list.DeleteAllItems()
		index = -1
		from bdb import Breakpoint
		for bplist in Breakpoint.bplist.values():
			for bp in bplist:
				baseName = os.path.split(bp.file)[1]
				cond = bp.cond
				item = index+1, 0, 0, 0, str(cond), 0, id(bp)
				index = list.InsertItem(item)
				list.SetItemText(index, 1, "%s: %s" % (baseName, bp.line))

class DebuggerWatchWindow(DebuggerListViewWindow):
	title = "Watch"
	columns = [ ("Expression", 70), ("Value", 1024)]

	def CreateWindow(self, parent):
		DebuggerListViewWindow.CreateWindow(self, parent)
		items = string.split(win32ui.GetProfileVal("Debugger Windows\\" + self.title, "Items", ""), "\t")
		index = -1
		for item in items:
			if item:
				index = self.InsertItem(index+1, item)
		self.InsertItem(index+1, "<New Item>")

	def SaveState(self):
		items = []
		for i in range(self.GetItemCount()-1):
			items.append(self.GetItemText(i,0))
		win32ui.WriteProfileVal("Debugger Windows\\" + self.title, "Items", string.join(items,"\t"))
		return 1

	def OnListEndLabelEdit(self, std, extra):
		item = extra[0]
		itemno = item[0]
		text = item[4]
		if text is None: return
		self.SetItemText(itemno, 0, text)
		if itemno == self.GetItemCount()-1:
			self.InsertItem(itemno+1, "<New Item>")
		self.RespondDebuggerState(self.debugger.debuggerState)

	def DeleteSelected(self):
		try:
			num = self.GetNextItem(-1, commctrl.LVNI_SELECTED)
			if num < self.GetItemCount()-1: # We cant delete the last
				self.DeleteItem(num)
		except win32ui.error:
			win32api.MessageBeep()

	def RespondDebuggerState(self, state):
		globs = locs = None
		if state==DBGSTATE_BREAK:
			if self.debugger.curframe:
				globs = self.debugger.curframe.f_globals
				locs = self.debugger.curframe.f_locals
		elif state==DBGSTATE_NOT_DEBUGGING:
			import __main__
			globs = locs = __main__.__dict__
		for i in range(self.GetItemCount()-1):
			text = self.GetItemText(i, 0)
			if globs is None:
				val = ""
			else:
				try:
					val = repr( eval( text, globs, locs) )
				except SyntaxError:
					val = "Syntax Error"
				except:
					t, v, tb = sys.exc_info()
					val = string.strip(traceback.format_exception_only(t, v)[0])
					tb = None # prevent a cycle.
			self.SetItemText(i, 1, val)

def CreateDebuggerDialog(parent, klass, debugger):
	control = klass(debugger)
	control.CreateWindow(parent)
	return control

DebuggerDialogInfos = (
	(0xe810, DebuggerStackWindow, None),
	(0xe811, DebuggerBreakpointsWindow, (10, 10)),
	(0xe812, DebuggerWatchWindow, None),
	)

SKIP_NONE=0
SKIP_STEP=1
SKIP_RUN=2

debugger_parent=pdb.Pdb
class Debugger(debugger_parent):
	def __init__(self):
		self.inited = 0
		self.skipBotFrame = SKIP_NONE
		self.userbotframe = None
		self.frameShutdown = 0
		self.pumping = 0
		self.debuggerState = DBGSTATE_NOT_DEBUGGING # Assume so, anyway.
		self.shownLineCurrent = None # The last filename I highlighted.
		self.shownLineCallstack = None # The last filename I highlighted.
		self.last_cmd_debugged = ""
		self.abortClosed = 0
		debugger_parent.__init__(self)

		# See if any break-points have been set in the editor
		for doc in editor.editorTemplate.GetDocumentList():
			lineNo = -1
			while 1:
				lineNo = doc.MarkerGetNext(lineNo+1, MARKER_BREAKPOINT)
				if lineNo <= 0: break
				self.set_break(doc.GetPathName(), lineNo)

		self.reset()
		self.inForcedGUI = win32ui.GetApp().IsInproc()
		self.options = LoadDebuggerOptions()
		self.bAtException = self.bAtPostMortem = 0

	def __del__(self):
		self.close()
	def close(self, frameShutdown = 0):
		# abortClose indicates if we have total shutdown
		# (ie, main window is dieing)
		if self.pumping:
			# Can stop pump here, as it only posts a message, and
			# returns immediately.
			if not self.StopDebuggerPump(): # User cancelled close.
				return 0
			# NOTE - from this point on the close can not be
			# stopped - the WM_QUIT message is already in the queue.
		self.frameShutdown = frameShutdown
		if not self.inited: return 1
		self.inited = 0

		SetInteractiveContext(None, None)

		frame = win32ui.GetMainFrame()
		frame.SaveBarState("ToolbarDebugging")
		# Hide the debuger toolbars (as they wont normally form part of the main toolbar state.
		for id, klass, float in DebuggerDialogInfos:
			try:
				tb = frame.GetControlBar(id)
				if tb.dialog is not None: # We may never have actually been shown.
					tb.dialog.SaveState()
					frame.ShowControlBar(tb, 0, 1)
			except win32ui.error:
				pass

		# Restore the standard toolbar config
		try:
			frame.LoadBarState("ToolbarDefault")
		except win32ui.error, msg: # When once created toolbars no longer exist.
			pass
#			print msg # LoadBarState failed (with win32 exception!)
		self._UnshowCurrentLine()
		self.set_quit()
		return 1

	def StopDebuggerPump(self):
		assert self.pumping, "Can't stop the debugger pump if Im not pumping!"
		# After stopping a pump, I may never return.
		if self.GUIAboutToFinishInteract():
			self.pumping = 0
			win32ui.StopDebuggerPump() # Posts a message, so we do return.
			return 1
		return 0

	def get_option(self, option):
		"""Public interface into debugger options
		"""
		try:
			return self.options[option]
		except KeyError:
			raise error, "Option %s is not a valid option" % option

	def prep_run(self, cmd):
		pass
	def done_run(self, cmd=None):
		self.RespondDebuggerState(DBGSTATE_NOT_DEBUGGING)
		self.close()
	def canonic(self, fname):
		return string.lower(os.path.abspath(fname))
	def reset(self):
		debugger_parent.reset(self)
		self.userbotframe = None
		self.UpdateAllLineStates()
		self._UnshowCurrentLine()


	def setup(self, f, t):
		debugger_parent.setup(self, f, t)
		self.bAtException = t is not None

	def set_break(self, filename, lineno, temporary=0, cond = None):
		filename = self.canonic(filename)
		self.SetLineState(filename, lineno, LINESTATE_BREAKPOINT)
		return debugger_parent.set_break(self, filename, lineno, temporary, cond)

	def clear_break(self, filename, lineno):
		filename = self.canonic(filename)
		self.ResetLineState(filename, lineno, LINESTATE_BREAKPOINT)
		return debugger_parent.clear_break(self, filename, lineno)

	def cmdloop(self):
		if self.frameShutdown: return # App in the process of closing - never break in!
		self.GUIAboutToBreak()

	def print_stack_entry(self, frame):
		# We dont want a stack printed - our GUI is better :-)
		pass

	def user_return(self, frame, return_value):
		# Same as parent, just no "print"
		# This function is called when a return trap is set here
		frame.f_locals['__return__'] = return_value
		self.interaction(frame, None)

	def user_exception(self, frame, (exc_type, exc_value, exc_traceback)):
		# This function is called if an exception occurs,
		# but only if we are to stop at or just below this level
		if self.get_option(OPT_STOP_EXCEPTIONS):
			frame.f_locals['__exception__'] = exc_type, exc_value
			print "Unhandled exception while debugging..."
			traceback.print_exception(exc_type, exc_value, exc_traceback)
			self.interaction(frame, exc_traceback)

	def user_line(self, frame):
		if frame.f_lineno==0: return
		debugger_parent.user_line(self, frame)

	def stop_here(self, frame):
		if frame is self.botframe and self.skipBotFrame == SKIP_RUN:
			self.set_continue()
			return 0
		if frame is self.botframe and self.skipBotFrame == SKIP_STEP:
			self.set_step()
			return 0
		return debugger_parent.stop_here(self, frame)

	def run(self, cmd,globals=None, locals=None, start_stepping = 1):
		if type(cmd) not in [types.StringType, types.CodeType]:
			raise TypeError, "Only strings can be run"
		self.last_cmd_debugged = cmd
		try:
			if globals is None:
				import __main__
				globals = __main__.__dict__
			if locals is None:
				locals = globals
			self.reset()
			self.prep_run(cmd)
			sys.settrace(self.trace_dispatch)
			if type(cmd) <> types.CodeType:
				cmd = cmd+'\n'
			try:
				try:
					if start_stepping: self.skipBotFrame = SKIP_STEP
					else: self.skipBotFrame = SKIP_RUN
					_doexec(cmd, globals, locals)
				except bdb.BdbQuit:
					pass
			finally:
				self.skipBotFrame = SKIP_NONE
				self.quitting = 1
				sys.settrace(None)

		finally:
			self.done_run(cmd)

	def runeval(self, expr, globals=None, locals=None):
		self.prep_run(expr)
		try:
			debugger_parent.runeval(self, expr, globals, locals)
		finally:
			self.done_run(expr)

	def runexec(self, what, globs=None, locs=None):
		self.reset()
		sys.settrace(self.trace_dispatch)
		try:
			try:
				exec what in globs, locs
			except bdb.BdbQuit:
				pass
		finally:
			self.quitting = 1
			sys.settrace(None)

	def do_set_step(self):
		if self.GUIAboutToRun():
			self.set_step()

	def do_set_next(self):
		if self.GUIAboutToRun():
			self.set_next(self.curframe)

	def do_set_return(self):
		if self.GUIAboutToRun():
			self.set_return(self.curframe)

	def do_set_continue(self):
		if self.GUIAboutToRun():
			self.set_continue()

	def set_quit(self):
		ok = 1
		if self.pumping:
			ok = self.StopDebuggerPump()
		if ok:
			debugger_parent.set_quit(self)

	def _dump_frame_(self, frame,name=None):
		if name is None: name = ""
		if frame:
			if frame.f_code and frame.f_code.co_filename:
				fname = os.path.split(frame.f_code.co_filename)[1]
			else:
				fname = "??"
			print `name`, fname, frame.f_lineno, frame
		else:
			print `name`, "None"

	def set_trace(self):
		# Start debugging from _2_ levels up!
		try:
			1 + ''
		except:
			frame = sys.exc_info()[2].tb_frame.f_back.f_back
		self.reset()
		self.userbotframe = None
		while frame:
			# scriptutils.py creates a local variable with name
			# '_debugger_stop_frame_', and we dont go past it
			# (everything above this is Pythonwin framework code)
			if frame.f_locals.has_key("_debugger_stop_frame_"):
				self.userbotframe = frame
				break

			frame.f_trace = self.trace_dispatch
			self.botframe = frame
			frame = frame.f_back
		self.set_step()
		sys.settrace(self.trace_dispatch)

	def set_cur_frame(self, frame):
		# Sets the "current" frame - ie, the frame with focus.  This is the
		# frame on which "step out" etc actions are taken.
		# This may or may not be the top of the stack.
		assert frame is not None, "You must pass a valid frame"
		self.curframe = frame
		for f, index in self.stack:
			if f is frame:
				self.curindex = index
				break
		else:
			assert 0, "Can't find the frame in the stack."
		SetInteractiveContext(frame.f_globals, frame.f_locals)
		self.GUIRespondDebuggerData()
		self.ShowCurrentLine()

	def IsBreak(self):
		return self.debuggerState == DBGSTATE_BREAK

	def IsDebugging(self):
		return self.debuggerState != DBGSTATE_NOT_DEBUGGING

	def RespondDebuggerState(self, state):
		if state == self.debuggerState: return
		if state==DBGSTATE_NOT_DEBUGGING: # Debugger exists, but not doing anything
			title = ""
		elif state==DBGSTATE_RUNNING: # Code is running under the debugger.
			title = " - running"
		elif state==DBGSTATE_BREAK: # We are at a breakpoint or stepping or whatever.
			if self.bAtException:
				if self.bAtPostMortem:
					title = " - post mortem exception"
				else:
					title = " - exception"
			else:
				title = " - break"
		else:
			raise error, "Invalid debugger state passed!"
		win32ui.GetMainFrame().SetWindowText(win32ui.LoadString(win32ui.IDR_MAINFRAME) + title)
		if self.debuggerState == DBGSTATE_QUITTING and state != DBGSTATE_NOT_DEBUGGING:
			print "Ignoring state change cos Im trying to stop!", state
			return
		self.debuggerState = state
		try:
			frame = win32ui.GetMainFrame()
		except win32ui.error:
			frame = None
		if frame is not None:
			for id, klass, float in DebuggerDialogInfos:
				cb = win32ui.GetMainFrame().GetControlBar(id).dialog
				cb.RespondDebuggerState(state)
		# Tell each open editor window about the state transition
		for doc in editor.editorTemplate.GetDocumentList():
			doc.OnDebuggerStateChange(state)
		self.ShowCurrentLine()

	#
	# GUI debugger interface.
	#
	def GUICheckInit(self):
		if self.inited: return
		self.inited = 1
		frame = win32ui.GetMainFrame()

		# And the other dockable debugger dialogs.
		for id, klass, float in DebuggerDialogInfos:
			try:
				frame.GetControlBar(id)
				exists=1
			except win32ui.error:
				exists=0
			if exists: continue
			bar = pywin.docking.DockingBar.DockingBar()
			bar.CreateWindow(win32ui.GetMainFrame(), CreateDebuggerDialog, klass.title, id, childCreatorArgs=(klass, self))
			bar.SetBarStyle( bar.GetBarStyle()|afxres.CBRS_TOOLTIPS|afxres.CBRS_FLYBY|afxres.CBRS_SIZE_DYNAMIC)
			bar.EnableDocking(afxres.CBRS_ALIGN_ANY)
			if float is None:
				frame.DockControlBar(bar)
			else:
				frame.FloatControlBar(bar, float, afxres.CBRS_ALIGN_ANY)
		try:
			frame.LoadBarState("ToolbarDebugging")
		except win32ui.error, details:
			print "LoadBarState failed - %s" % details

		# ALWAYS show debugging toolbar, regardless of saved state
		tb = frame.GetControlBar(win32ui.ID_VIEW_TOOLBAR_DBG)
		frame.ShowControlBar(tb, 1, 1)
		self.GUIRespondDebuggerData()

#		frame.RecalcLayout()

	def GetDebuggerBar(self, barName):
		frame = win32ui.GetMainFrame()
		for id, klass, float in DebuggerDialogInfos:
			if klass.title == barName:
				return frame.GetControlBar(id)
		assert 0, "Can't find a bar of that name!"

	def GUIRespondDebuggerData(self):
		if not self.inited: # GUI not inited - no toolbars etc.
			return

		for id, klass, float in DebuggerDialogInfos:
			cb = win32ui.GetMainFrame().GetControlBar(id).dialog
			cb.RespondDebuggerData()

	def GUIAboutToRun(self):
		if not self.StopDebuggerPump():
			return 0
		self._UnshowCurrentLine()
		self.RespondDebuggerState(DBGSTATE_RUNNING)
		SetInteractiveContext(None, None)
		return 1

	def GUIAboutToBreak(self):
		"Called as the GUI debugger is about to get context, and take control of the running program."
		self.GUICheckInit()
		self.RespondDebuggerState(DBGSTATE_BREAK)
		self.GUIAboutToInteract()
		if self.pumping:
			print "!!! Already pumping - outa here"
			return
		self.pumping = 1
		win32ui.StartDebuggerPump() # NOTE - This will NOT return until the user is finished interacting
		assert not self.pumping, "Should not be pumping once the pump has finished"
		if self.frameShutdown: # User shut down app while debugging
			win32ui.GetMainFrame().PostMessage(win32con.WM_CLOSE)

	def GUIAboutToInteract(self):
		"Called as the GUI is about to perform any interaction with the user"
		frame = win32ui.GetMainFrame()
		# Remember the enabled state of our main frame
		# may be disabled primarily if a modal dialog is displayed.
		# Only get at enabled via GetWindowLong.
		self.bFrameEnabled = frame.IsWindowEnabled()
		self.oldForeground = None
		fw = win32ui.GetForegroundWindow()
		if fw is not frame:
			self.oldForeground = fw
#			fw.EnableWindow(0) Leave enabled for now?
			self.oldFrameEnableState = frame.IsWindowEnabled()
			frame.EnableWindow(1)
		if self.inForcedGUI and not frame.IsWindowVisible():
			frame.ShowWindow(win32con.SW_SHOW)
			frame.UpdateWindow()
		if self.curframe:
			SetInteractiveContext(self.curframe.f_globals, self.curframe.f_locals)
		else:
			SetInteractiveContext(None, None)
		self.GUIRespondDebuggerData()

	def GUIAboutToFinishInteract(self):
		"""Called as the GUI is about to finish any interaction with the user
		   Returns non zero if we are allowed to stop interacting"""
		if self.oldForeground is not None:
			win32ui.GetMainFrame().EnableWindow(self.oldFrameEnableState)
			self.oldForeground.EnableWindow(1)
#			self.oldForeground.SetForegroundWindow() - fails??
		if not self.inForcedGUI:
			return 1 # Never a problem, and nothing else to do.
		# If we are running a forced GUI, we may never get an opportunity
		# to interact again.  Therefore we perform a "SaveAll", to makesure that
		# any documents are saved before leaving.
		for template in win32ui.GetApp().GetDocTemplateList():
			for doc in template.GetDocumentList():
				if not doc.SaveModified():
					return 0
		# All documents saved - now hide the app and debugger.
		if self.get_option(OPT_HIDE):
			frame = win32ui.GetMainFrame()
			frame.ShowWindow(win32con.SW_HIDE)
		return 1

	#
	# Pythonwin interface - all stuff to do with showing source files,
	# changing line states etc.
	#
	def ShowLineState(self, fileName, lineNo, lineState):
		# Set the state of a line, open if not already
		self.ShowLineNo(fileName, lineNo)
		self.SetLineState(fileName, lineNo, lineState)

	def SetLineState(self, fileName, lineNo, lineState):
		# Set the state of a line if the document is open.
		doc = editor.editorTemplate.FindOpenDocument(fileName)
		if doc is not None:
			marker = _LineStateToMarker(lineState)
			if not doc.MarkerCheck(lineNo, marker):
				doc.MarkerAdd(lineNo, marker)

	def ResetLineState(self, fileName, lineNo, lineState):
		# Set the state of a line if the document is open.
		doc = editor.editorTemplate.FindOpenDocument(fileName)
		if doc is not None:
			marker = _LineStateToMarker(lineState)
			doc.MarkerDelete(lineNo, marker)

	def UpdateDocumentLineStates(self, doc):
		# Show all lines in their special status color.  If the doc is open
		# all line states are reset.
		doc.MarkerDeleteAll( MARKER_BREAKPOINT )
		doc.MarkerDeleteAll( MARKER_CURRENT )
		fname = self.canonic(doc.GetPathName())
		# Now loop over all break-points
		for line in self.breaks.get(fname, []):
			doc.MarkerAdd(line, MARKER_BREAKPOINT)
		# And the current line if in this document.
		if self.shownLineCurrent and fname == self.shownLineCurrent[0]:
			lineNo = self.shownLineCurrent[1]
			if not doc.MarkerCheck(lineNo, MARKER_CURRENT):
				doc.MarkerAdd(lineNo, MARKER_CURRENT)
#		if self.shownLineCallstack and fname == self.shownLineCallstack[0]:
#			doc.MarkerAdd(self.shownLineCallstack[1], MARKER_CURRENT)

	def UpdateAllLineStates(self):
		for doc in editor.editorTemplate.GetDocumentList():
			self.UpdateDocumentLineStates(doc)

	def ShowCurrentLine(self):
		# Show the current line.  Only ever 1 current line - undoes last current
		# The "Current Line" is self.curframe.
		# The "Callstack Line" is the top of the stack.
		# If current == callstack, only show as current.
		self._UnshowCurrentLine() # un-highlight the old one.
		if self.curframe:
			fileName = self.canonic(self.curframe.f_code.co_filename)
			lineNo = self.curframe.f_lineno
			self.shownLineCurrent = fileName, lineNo
			self.ShowLineState(fileName, lineNo, LINESTATE_CURRENT)

	def _UnshowCurrentLine(self):
		"Unshow the current line, and forget it"
		if self.shownLineCurrent is not None:
			fname, lineno = self.shownLineCurrent
			self.ResetLineState(fname, lineno, LINESTATE_CURRENT)
			self.shownLineCurrent = None

	def ShowLineNo( self, filename, lineno ):
		wasOpen = editor.editorTemplate.FindOpenDocument(filename) is not None
		if os.path.isfile(filename) and scriptutils.JumpToDocument(filename, lineno):
			if not wasOpen:
				doc = editor.editorTemplate.FindOpenDocument(filename)
				if doc is not None:
					self.UpdateDocumentLineStates(doc)
					return 1
				return 0
			return 1
		else:
			# Can't find the source file - linecache may have it?
			import linecache
			line = linecache.getline(filename, lineno)
			print "%s(%d): %s" % (os.path.basename(filename), lineno, string.expandtabs(line[:-1],4))
			return 0

def _doexec(cmd, globals, locals):
	exec cmd in globals, locals