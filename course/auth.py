# -*- coding: utf-8 -*-

from __future__ import division

__copyright__ = "Copyright (C) 2014 Andreas Kloeckner"

__license__ = """
Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in
all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
THE SOFTWARE.
"""

from django.shortcuts import (  # noqa
        render, get_object_or_404, redirect)
from django.contrib import messages
import django.forms as forms
from django.core.exceptions import PermissionDenied, SuspiciousOperation
from crispy_forms.helper import FormHelper
from crispy_forms.layout import Submit
from django.db.models import Q
from django.conf import settings
from django.contrib.auth.models import User
from django.contrib.auth.forms import \
        AuthenticationForm as AuthenticationFormBase
from django.contrib.auth.decorators import user_passes_test
from django.core.urlresolvers import reverse

from course.models import (
        UserStatus, user_status,
        Participation, participation_role, participation_status,
        )

from relate.utils import StyledForm, StyledModelForm


# {{{ impersonation

def may_impersonate(user):
    return user.is_staff


def whom_may_impersonate(impersonator):
    if impersonator.is_superuser:
        return User.objects.filter(
                participation__status=participation_status.active)

    my_privileged_participations = Participation.objects.filter(
            user=impersonator,
            status=participation_status.active,
            role__in=(
                participation_role.instructor,
                participation_role.teaching_assistant))

    q_object = None

    for part in my_privileged_participations:
        if part.role == participation_role.instructor:
            impersonable_roles = (
                participation_role.teaching_assistant,
                participation_role.student)
        elif part.role == participation_role.teaching_assistant:
            impersonable_roles = (
                participation_role.student,)
        else:
            assert False

        part_q_object = Q(
                participation__course=part.course,
                participation__status=participation_status.active,
                participation__role__in=impersonable_roles)

        if q_object is None:
            q_object = part_q_object
        else:
            q_object = q_object | part_q_object

    return set(User.objects.filter(q_object).order_by("last_name"))


class ImpersonateMiddleware(object):
    def process_request(self, request):
        if request.user.is_staff and 'impersonate_id' in request.session:
            imp_id = request.session['impersonate_id']

            request.relate_impersonate_original_user = request.user
            if imp_id is not None:
                impersonees = whom_may_impersonate(request.user)
                if any(u.id == imp_id for u in impersonees):
                    request.user = User.objects.get(id=imp_id)
                else:
                    messages.add_message(request, messages.ERROR,
                            "Error while impersonating.")


class ImpersonateForm(StyledForm):
    def __init__(self, impersonator, *args, **kwargs):
        super(ImpersonateForm, self).__init__(*args, **kwargs)

        impersonees = whom_may_impersonate(impersonator)

        self.fields["user"] = forms.ChoiceField(
                choices=[
                    (u.id, "%s - %s, %s" % (u.email, u.last_name, u.first_name))
                    for u in sorted(impersonees,
                        key=lambda user: user.last_name.lower())
                    ],
                required=True,
                help_text="Select user to impersonate.")

        self.helper.add_input(Submit("submit", "Impersonate",
            css_class="col-lg-offset-2"))


@user_passes_test(may_impersonate)
def impersonate(request):
    if hasattr(request, "relate_impersonate_original_user"):
        messages.add_message(request, messages.ERROR,
                "Already impersonating someone.")
        return redirect("course.auth.stop_impersonating")

    if request.method == 'POST':
        form = ImpersonateForm(request.user, request.POST)
        if form.is_valid():
            user = User.objects.get(id=form.cleaned_data["user"])

            messages.add_message(request, messages.INFO,
                    "Now impersonating '%s'." % user.username)
            request.session['impersonate_id'] = user.id

            # Because we'll likely no longer have access to this page.
            return redirect("course.views.home")
    else:
        form = ImpersonateForm(request.user)

    return render(request, "generic-form.html", {
        "form_description": "Impersonate user",
        "form": form
        })


class StopImpersonatingForm(forms.Form):
    def __init__(self, *args, **kwargs):
        self.helper = FormHelper()
        super(StopImpersonatingForm, self).__init__(*args, **kwargs)

        self.helper.add_input(Submit("submit", "Stop impersonating"))


def stop_impersonating(request):
    if not hasattr(request, "relate_impersonate_original_user"):
        messages.add_message(request, messages.ERROR,
                "Not currently impersonating anyone.")
        return redirect("course.views.home")

    if request.method == 'POST':
        form = StopImpersonatingForm(request.POST)
        if form.is_valid():
            messages.add_message(request, messages.INFO,
                    "No longer impersonating anyone.")
            del request.session['impersonate_id']

            # Because otherwise the header will show stale data.
            return redirect("course.views.home")
    else:
        form = StopImpersonatingForm()

    return render(request, "generic-form.html", {
        "form_description": "Stop impersonating user",
        "form": form
        })


def impersonation_context_processor(request):
    return {
            "currently_impersonating":
            hasattr(request, "relate_impersonate_original_user"),
            }

# }}}


# {{{ conventional login

class LoginForm(AuthenticationFormBase):
    def __init__(self, *args, **kwargs):
        self.helper = FormHelper()
        self.helper.form_tag = False
        self.helper.label_class = "col-lg-2"
        self.helper.field_class = "col-lg-8"

        self.helper.add_input(Submit("submit", "Sign in",
            css_class="col-lg-offset-2"))

        super(LoginForm, self).__init__(*args, **kwargs)


def sign_in(request):
    from django.contrib.auth.views import login
    return login(request, template_name="course/login.html",
            authentication_form=LoginForm)

# }}}


# {{{ email sign-in flow

class SignInByEmailForm(StyledForm):
    email = forms.EmailField(required=True)

    def __init__(self, *args, **kwargs):
        super(SignInByEmailForm, self).__init__(*args, **kwargs)

        self.helper.add_input(
                Submit("submit", "Send sign-in email",
                    css_class="col-lg-offset-2"))


def make_sign_in_key(user):
    # Try to ensure these hashes aren't guessable.
    import random
    import hashlib
    from time import time
    m = hashlib.sha1()
    m.update(user.email)
    m.update(hex(random.getrandbits(128)))
    m.update(str(time()))
    return m.hexdigest()


def sign_in_by_email(request):
    if settings.STUDENT_SIGN_IN_VIEW != "course.auth.sign_in_by_email":
        raise SuspiciousOperation("email-based sign-in is not being used")

    if request.method == 'POST':
        form = SignInByEmailForm(request.POST)
        if form.is_valid():
            from django.contrib.auth.models import User

            email = form.cleaned_data["email"]
            user, created = User.objects.get_or_create(
                    email__iexact=email,
                    defaults=dict(username=email, email=email))

            if created:
                user.set_unusable_password()
                user.save()

            ustatus, ustatus_created = UserStatus.objects.get_or_create(
                    user=user,
                    defaults=dict(
                        status=user_status.unconfirmed,
                        sign_in_key=make_sign_in_key(user)))

            if not created:
                ustatus.sign_in_key = make_sign_in_key(user)
                ustatus.save()

            from django.template.loader import render_to_string
            message = render_to_string("course/sign-in-email.txt", {
                "user": user,
                "sign_in_uri": request.build_absolute_uri(
                    reverse(
                        "course.auth.sign_in_stage2_with_token",
                        args=(user.id, ustatus.sign_in_key,))),
                "home_uri": request.build_absolute_uri(reverse("course.views.home"))
                })
            from django.core.mail import send_mail
            send_mail("Your RELATE sign-in link", message,
                    settings.ROBOT_EMAIL_FROM, recipient_list=[email])

            messages.add_message(request, messages.INFO,
                    "Email sent. Please check your email and click the link.")

            return redirect("course.views.home")
    else:
        form = SignInByEmailForm()

    return render(request, "course/login-by-email.html", {
        "form_description": "",
        "form": form
        })


class TokenBackend(object):
    def authenticate(self, user_id=None, token=None):
        user = User.objects.get(id=user_id)
        ustatuses = UserStatus.objects.filter(
                user=user, sign_in_key=token)

        assert ustatuses.count() <= 1
        if ustatuses.count() == 0:
            return None

        (ustatus,) = ustatuses

        ustatus.status = user_status.active
        ustatus.sign_in_key = None
        ustatus.save()

        return ustatus.user

    def get_user(self, user_id):
        try:
            return User.objects.get(pk=user_id)
        except User.DoesNotExist:
            return None


def sign_in_stage2_with_token(request, user_id, sign_in_key):
    if settings.STUDENT_SIGN_IN_VIEW != "course.auth.sign_in_by_email":
        raise SuspiciousOperation("email-based sign-in is not being used")

    from django.contrib.auth import authenticate, login
    user = authenticate(user_id=int(user_id), token=sign_in_key)
    if user is None:
        messages.add_message(request, messages.ERROR,
                "Invalid sign-in token. Perhaps you've used an old token email?")
        raise PermissionDenied("invalid sign-in token")

    if not user.is_active:
        messages.add_message(request, messages.ERROR,
                "Account disabled.")
        raise PermissionDenied("invalid sign-in token")

    login(request, user)

    if not (user.first_name and user.last_name):
        messages.add_message(request, messages.INFO,
                "Successfully signed in. "
                "Please complete your registration information below.")

        return redirect("course.auth.user_profile")
    else:
        messages.add_message(request, messages.INFO,
                "Successfully signed in.")

        return redirect("course.views.home")


# }}}


# {{{ user profile

class UserProfileForm(StyledModelForm):
    class Meta:
        model = User
        fields = ("first_name", "last_name")

    def __init__(self, *args, **kwargs):
        super(UserProfileForm, self).__init__(*args, **kwargs)

        self.helper.add_input(
                Submit("submit", "Update",
                    css_class="col-lg-offset-2"))


def user_profile(request):
    if not request.user.is_authenticated():
        raise PermissionDenied()

    if request.method == "POST":
        form = UserProfileForm(request.POST, instance=request.user)
        if form.is_valid():
            form.save()
            messages.add_message(request, messages.INFO,
                    "Profile data saved.")
            return redirect("course.views.home")

    # if a GET (or any other method) we'll create a blank form
    else:
        form = UserProfileForm(instance=request.user)

    return render(request, "user-profile-form.html", {
        "form": form,
        })

# }}}


def get_role_and_participation(request, course):
    # "wake up" lazy object
    # http://stackoverflow.com/questions/20534577/int-argument-must-be-a-string-or-a-number-not-simplelazyobject  # noqa
    user = (request.user._wrapped
            if hasattr(request.user, '_wrapped')
            else request.user)

    if not user.is_authenticated():
        return participation_role.unenrolled, None

    participations = list(Participation.objects.filter(
            user=user,
            course=course,
            status=participation_status.active
            ))

    # The uniqueness constraint should have ensured that.
    assert len(participations) <= 1

    if len(participations) == 0:
        return participation_role.unenrolled, None

    participation = participations[0]
    if participation.status != participation_status.active:
        return participation_role.unenrolled, participation
    else:
        return participation.role, participation


# vim: foldmethod=marker
