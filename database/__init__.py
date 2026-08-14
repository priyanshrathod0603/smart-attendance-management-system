from .db import db
from .models import (
    Attendance,
    Camera,
    Student,
    Teacher,
    UnknownFace,
)

# Importing time_management registers the new tables with SQLAlchemy's
# metadata so ``db.create_all()`` picks them up automatically. We keep
# this import additive — the existing models above continue to work
# byte-for-byte as before.
from time_management.models import (  # noqa: F401
    AttendanceAuditLog,
    AttendanceHoliday,
    AttendanceSession,
    AttendanceTimeRule,
)
from .enterprise_models import (  # noqa: F401
    StudentAttendance, TeacherAttendance, AttendanceEvent, TeacherTimeRule,
    StudentTimeRule, HolidayCalendar, WeeklyOff, AttendanceSetting,
    AttendanceLog, AttendanceCorrection, CameraSession, Notification,
)
from .auth_models import User, AuthSession, AuthToken, OAuthIdentity, LoginEvent  # noqa: F401

# Importing hr.models registers all HR + Payroll tables with SQLAlchemy's
# metadata so ``db.create_all()`` creates them automatically. This is
# entirely additive — no existing table is modified.
from hr.models import (  # noqa: F401
    Department, Designation, Employee, EmployeeEmploymentHistory,
    SalaryComponent, EmployeeSalaryStructure, EmployeeSalaryComponent,
    LeaveType, EmployeeLeaveBalance, LeaveRequest,
    Shift, EmployeeShift, OvertimeRecord,
    EmployeeBonus, EmployeeLoan, LoanRepayment, Reimbursement,
    PayrollPeriod, PayrollRecord, PayrollAttendanceSummary,
    PayrollEarning, PayrollDeduction, Payslip, HRAuditLog,
)

__all__ = [
    "db",
    "Student",
    "Camera",
    "Attendance",
    "Teacher",
    "UnknownFace",
    "AttendanceAuditLog",
    "AttendanceHoliday",
    "AttendanceSession",
    "AttendanceTimeRule",
]
